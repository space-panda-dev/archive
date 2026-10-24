{-# LANGUAGE OverloadedStrings #-}

-- | The regular expressions a contract's @pattern@ keyword may use, and
-- their matcher. Pure: nothing here reads a schema beyond the pattern text.
module Archive.Door.Contracts.Pattern
  ( ContractPattern
  , PatternError (..)
  , renderPatternError
  , compilePattern
  , matchesPattern
  ) where

import Data.Aeson (Value (..))
import Data.Text (Text)
import Data.Text qualified as Text

-- Contract patterns use a small, closed regular-expression subset instead of
-- a regex engine with dialect-dependent behaviour: anchored patterns built from
-- literals, escaped metacharacters, ASCII character classes without negation,
-- groups with alternation, and the quantifiers ?, *, +, {n}, and {n,m}.  Any
-- other syntax is an unsupported contract, rejected while the bundle loads.
data PatternNode
  = PatternLiteral Char
  | PatternClass [Char]
  | PatternGroup [[PatternNode]]
  | PatternRepeat PatternNode Int (Maybe Int)

newtype ContractPattern = ContractPattern [PatternNode]

-- | Why a pattern is outside the supported subset. The bundle is refused
-- with the text 'renderPatternError' gives, which the suites compare.
data PatternError
  = PatternNotString
  | UnbalancedGroup
  | NotAnchored
  | UnsupportedEscape
  | UnterminatedClass
  | UnsupportedGroupSyntax
  | UnsupportedSyntax
  | Incomplete
  | EmptyClass
  | UnsupportedClass
  | UnterminatedQuantifier
  | InvalidCount
  deriving (Eq, Show)

renderPatternError :: PatternError -> Text
renderPatternError problem = "contract schema pattern " <> case problem of
  PatternNotString -> "must be a string"
  UnbalancedGroup -> "has an unbalanced group"
  NotAnchored -> "must be anchored"
  UnsupportedEscape -> "uses an unsupported escape"
  UnterminatedClass -> "has an unterminated class"
  UnsupportedGroupSyntax -> "uses unsupported group syntax"
  UnsupportedSyntax -> "uses unsupported syntax"
  Incomplete -> "is incomplete"
  EmptyClass -> "has an empty class"
  UnsupportedClass -> "uses an unsupported character class"
  UnterminatedQuantifier -> "has an unterminated quantifier"
  InvalidCount -> "quantifier must be a valid count"

compilePattern :: Value -> Either PatternError ContractPattern
compilePattern (String patternText) = parsePattern patternText
compilePattern _ = Left PatternNotString

parsePattern :: Text -> Either PatternError ContractPattern
parsePattern source = case Text.uncons source of
  Just ('^', rest) | Just (body, '$') <- Text.unsnoc rest -> do
    (alternatives, remaining) <- parseAlternatives (Text.unpack body)
    case (alternatives, remaining) of
      ([sequence'], []) -> Right (ContractPattern sequence')
      (_, []) -> Right (ContractPattern [PatternGroup alternatives])
      _ -> Left UnbalancedGroup
  _ -> Left NotAnchored

-- | Parse alternatives up to an unconsumed ')' or the end of input.
parseAlternatives :: String -> Either PatternError ([[PatternNode]], String)
parseAlternatives input = do
  (first, rest) <- parseSequence input
  case rest of
    '|' : more -> do
      (others, remaining) <- parseAlternatives more
      Right (first : others, remaining)
    _ -> Right ([first], rest)

parseSequence :: String -> Either PatternError ([PatternNode], String)
parseSequence input = case input of
  [] -> Right ([], [])
  ')' : _ -> Right ([], input)
  '|' : _ -> Right ([], input)
  _ -> do
    (atom, afterAtom) <- parseAtom input
    (node, afterQuantifier) <- parseQuantifier atom afterAtom
    (nodes, rest) <- parseSequence afterQuantifier
    Right (node : nodes, rest)

parseAtom :: String -> Either PatternError (PatternNode, String)
parseAtom input = case input of
  '\\' : character : rest
    | character `elem` ('-' : metaCharacters) -> Right (PatternLiteral character, rest)
    | otherwise -> Left UnsupportedEscape
  '[' : rest -> case break (== ']') rest of
    (body, ']' : afterClass) -> do
      characters <- parseClass body
      Right (PatternClass characters, afterClass)
    _ -> Left UnterminatedClass
  '(' : '?' : _ -> Left UnsupportedGroupSyntax
  '(' : rest -> do
    (alternatives, remaining) <- parseAlternatives rest
    case remaining of
      ')' : afterGroup -> Right (PatternGroup alternatives, afterGroup)
      _ -> Left UnbalancedGroup
  character : rest
    | character `elem` metaCharacters -> Left UnsupportedSyntax
    | otherwise -> Right (PatternLiteral character, rest)
  [] -> Left Incomplete

metaCharacters :: String
metaCharacters = ".*+?()|{}[]\\^$"

-- | A class is a nonempty run of ASCII letters and digits and ranges between
-- them; negation and escapes inside classes are unsupported.
parseClass :: String -> Either PatternError [Char]
parseClass [] = Left EmptyClass
parseClass body = go body
  where
    go [] = Right []
    go (low : '-' : high : rest)
      | classCharacter low && classCharacter high && low <= high = (enumFromTo low high <>) <$> go rest
      | otherwise = Left UnsupportedClass
    go (character : rest)
      | classCharacter character = (character :) <$> go rest
      | otherwise = Left UnsupportedClass
    classCharacter character =
      ('0' <= character && character <= '9') || ('a' <= character && character <= 'z')
        || ('A' <= character && character <= 'Z')

parseQuantifier :: PatternNode -> String -> Either PatternError (PatternNode, String)
parseQuantifier atom input = case input of
  '?' : rest -> Right (PatternRepeat atom 0 (Just 1), rest)
  '*' : rest -> Right (PatternRepeat atom 0 Nothing, rest)
  '+' : rest -> Right (PatternRepeat atom 1 Nothing, rest)
  '{' : rest -> case break (== '}') rest of
    (bounds, '}' : afterBounds) -> do
      (lower, upper) <- parseBounds bounds
      Right (PatternRepeat atom lower upper, afterBounds)
    _ -> Left UnterminatedQuantifier
  _ -> Right (atom, input)

parseBounds :: String -> Either PatternError (Int, Maybe Int)
parseBounds bounds = case break (== ',') bounds of
  (exact, []) -> do
    count <- natural exact
    if count > 0 then Right (count, Just count) else invalid
  (lower, ',' : []) -> do
    count <- natural lower
    Right (count, Nothing)
  (lower, ',' : upper) -> do
    low <- natural lower
    high <- natural upper
    if high > 0 && low <= high then Right (low, Just high) else invalid
  _ -> invalid
  where
    natural digits
      | not (null digits) && all (\character -> '0' <= character && character <= '9') digits
          && length digits <= 6 = Right (read digits)
      | otherwise = invalid
    invalid = Left InvalidCount

matchesPattern :: ContractPattern -> Text -> Bool
matchesPattern (ContractPattern nodes) value = any null (matchSequence nodes (Text.unpack value))

-- | Every way the nodes can consume a prefix, as the remaining suffixes.
matchSequence :: [PatternNode] -> String -> [String]
matchSequence [] input = [input]
matchSequence (node : nodes) input = concatMap (matchSequence nodes) (matchNode node input)

matchNode :: PatternNode -> String -> [String]
matchNode node input = case node of
  PatternLiteral expected -> case input of
    character : rest | character == expected -> [rest]
    _ -> []
  PatternClass allowed -> case input of
    character : rest | character `elem` allowed -> [rest]
    _ -> []
  PatternGroup alternatives -> concatMap (`matchSequence` input) alternatives
  PatternRepeat inner lower upper -> repeatNode inner lower upper input

-- | Each further repetition must consume input, so an unbounded repeat of a
-- pattern that can match the empty string still terminates.
repeatNode :: PatternNode -> Int -> Maybe Int -> String -> [String]
repeatNode inner lower upper input
  | lower > 0 = concatMap (repeatNode inner (lower - 1) (subtract 1 <$> upper)) (matchNode inner input)
  | upper == Just 0 = [input]
  | otherwise = input : concatMap continue (matchNode inner input)
  where
    continue rest
      | length rest < length input = repeatNode inner 0 (subtract 1 <$> upper) rest
      | otherwise = []
