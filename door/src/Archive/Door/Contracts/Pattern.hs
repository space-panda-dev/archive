{-# LANGUAGE OverloadedStrings #-}

-- | The regular expressions a contract's @pattern@ keyword may use, and
-- their matcher. Pure: nothing here reads a schema beyond the pattern text.
module Archive.Door.Contracts.Pattern
  ( ContractPattern
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

compilePattern :: Value -> Either Text ContractPattern
compilePattern (String patternText) = parsePattern patternText
compilePattern _ = Left "contract schema pattern must be a string"

parsePattern :: Text -> Either Text ContractPattern
parsePattern source = case Text.uncons source of
  Just ('^', rest) | Just (body, '$') <- Text.unsnoc rest -> do
    (alternatives, remaining) <- parseAlternatives (Text.unpack body)
    case (alternatives, remaining) of
      ([sequence'], []) -> Right (ContractPattern sequence')
      (_, []) -> Right (ContractPattern [PatternGroup alternatives])
      _ -> Left "contract schema pattern has an unbalanced group"
  _ -> Left "contract schema pattern must be anchored"

-- | Parse alternatives up to an unconsumed ')' or the end of input.
parseAlternatives :: String -> Either Text ([[PatternNode]], String)
parseAlternatives input = do
  (first, rest) <- parseSequence input
  case rest of
    '|' : more -> do
      (others, remaining) <- parseAlternatives more
      Right (first : others, remaining)
    _ -> Right ([first], rest)

parseSequence :: String -> Either Text ([PatternNode], String)
parseSequence input = case input of
  [] -> Right ([], [])
  ')' : _ -> Right ([], input)
  '|' : _ -> Right ([], input)
  _ -> do
    (atom, afterAtom) <- parseAtom input
    (node, afterQuantifier) <- parseQuantifier atom afterAtom
    (nodes, rest) <- parseSequence afterQuantifier
    Right (node : nodes, rest)

parseAtom :: String -> Either Text (PatternNode, String)
parseAtom input = case input of
  '\\' : character : rest
    | character `elem` ('-' : metaCharacters) -> Right (PatternLiteral character, rest)
    | otherwise -> Left "contract schema pattern uses an unsupported escape"
  '[' : rest -> case break (== ']') rest of
    (body, ']' : afterClass) -> do
      characters <- parseClass body
      Right (PatternClass characters, afterClass)
    _ -> Left "contract schema pattern has an unterminated class"
  '(' : '?' : _ -> Left "contract schema pattern uses unsupported group syntax"
  '(' : rest -> do
    (alternatives, remaining) <- parseAlternatives rest
    case remaining of
      ')' : afterGroup -> Right (PatternGroup alternatives, afterGroup)
      _ -> Left "contract schema pattern has an unbalanced group"
  character : rest
    | character `elem` metaCharacters -> Left "contract schema pattern uses unsupported syntax"
    | otherwise -> Right (PatternLiteral character, rest)
  [] -> Left "contract schema pattern is incomplete"

metaCharacters :: String
metaCharacters = ".*+?()|{}[]\\^$"

-- | A class is a nonempty run of ASCII letters and digits and ranges between
-- them; negation and escapes inside classes are unsupported.
parseClass :: String -> Either Text [Char]
parseClass [] = Left "contract schema pattern has an empty class"
parseClass body = go body
  where
    go [] = Right []
    go (low : '-' : high : rest)
      | classCharacter low && classCharacter high && low <= high = (enumFromTo low high <>) <$> go rest
      | otherwise = Left "contract schema pattern uses an unsupported character class"
    go (character : rest)
      | classCharacter character = (character :) <$> go rest
      | otherwise = Left "contract schema pattern uses an unsupported character class"
    classCharacter character =
      ('0' <= character && character <= '9') || ('a' <= character && character <= 'z')
        || ('A' <= character && character <= 'Z')

parseQuantifier :: PatternNode -> String -> Either Text (PatternNode, String)
parseQuantifier atom input = case input of
  '?' : rest -> Right (PatternRepeat atom 0 (Just 1), rest)
  '*' : rest -> Right (PatternRepeat atom 0 Nothing, rest)
  '+' : rest -> Right (PatternRepeat atom 1 Nothing, rest)
  '{' : rest -> case break (== '}') rest of
    (bounds, '}' : afterBounds) -> do
      (lower, upper) <- parseBounds bounds
      Right (PatternRepeat atom lower upper, afterBounds)
    _ -> Left "contract schema pattern has an unterminated quantifier"
  _ -> Right (atom, input)

parseBounds :: String -> Either Text (Int, Maybe Int)
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
    invalid = Left "contract schema pattern quantifier must be a valid count"

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
