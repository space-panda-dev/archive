{-# LANGUAGE OverloadedStrings #-}

-- | Properties of the contract pattern engine ("Archive.Door.Contracts.Pattern").
--
-- A bundle's patterns are author input and every instance string is record
-- input, so the engine must be total on both, and its matching must be the
-- plain regular-language reading of the subset it accepts. The reference
-- below computes that reading over positions, a different method from the
-- engine's list of remaining suffixes, so the two can disagree only if one
-- of them is wrong.
module PatternProperties (patternProperties) where

import Archive.Door.Contracts.Pattern (compilePattern, matchesPattern, renderPatternError)
import Data.Aeson (Value (String))
import Data.List (intercalate)
import qualified Data.Set as Set
import qualified Data.Text as Text
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.QuickCheck
  ( Gen
  , Property
  , choose
  , counterexample
  , elements
  , forAll
  , frequency
  , listOf
  , oneof
  , resize
  , sized
  , sublistOf
  , suchThat
  , testProperty
  , vectorOf
  , (===)
  )

patternProperties :: TestTree
patternProperties = testGroup "patternProperties"
  [ testProperty "compiling and matching arbitrary text never crashes" $
      forAll patternishText $ \source -> forAll subjectText $ \subject ->
        case compilePattern (String source) of
          Left problem -> Text.length (renderPatternError problem) > 0
          Right compiled -> matchesPattern compiled subject || True
  , testProperty "matching agrees with the position-set reference" $
      forAll (sized (\size -> resize (min size 6) genPattern)) $ \candidate ->
        forAll subjectText $ \subject ->
          case compilePattern (String (Text.pack (renderPattern candidate))) of
            Left problem -> counterexample (renderPattern candidate <> ": " <> show problem) False
            Right compiled -> counterexample (renderPattern candidate)
              (matchesPattern compiled subject === referenceMatches candidate (Text.unpack subject))
  , testProperty "a bounded quantifier matches exactly its counts" quantifierBounds
  ]

-- | Short strings over the engine's own syntax, anchored most of the time, so
-- a useful share of them compile and reach the matcher.
patternishText :: Gen Text.Text
patternishText = do
  body <- resize 12 (listOf (elements "ab01-.,*+?|()[]{}\\^$"))
  anchored <- frequency [(4, pure True), (1, pure False)]
  pure (Text.pack (if anchored then "^" <> body <> "$" else body))

-- | Subjects stay short: an unbounded repeat of a group that can match the
-- empty string explores every split of the subject, which is exponential in
-- its length by design of the engine, not a defect these properties test.
subjectText :: Gen Text.Text
subjectText = Text.pack <$> (choose (0, 8) >>= (`vectorOf` elements subjectAlphabet))

subjectAlphabet :: String
subjectAlphabet = "abc.-"

-- | The accepted subset, as the test sees it. Repeats wrap atoms only, as the
-- grammar allows one quantifier per atom.
data Pattern = Pattern [[Node]]
  deriving (Show)

data Node
  = Literal Char
  | Class String
  | Group [[Node]]
  | Repeat Node Quantifier
  deriving (Show)

data Quantifier = Optional | Star | Plus | Exactly Int | AtLeast Int | Between Int Int
  deriving (Show)

genPattern :: Gen Pattern
genPattern = sized $ \size -> Pattern <$> genAlternatives size

genAlternatives :: Int -> Gen [[Node]]
genAlternatives size = do
  count <- choose (1, if size > 1 then 3 else 1)
  vectorOf count (genSequence size)

genSequence :: Int -> Gen [Node]
genSequence size = do
  count <- choose (0, 3)
  vectorOf count (genNode size)

genNode :: Int -> Gen Node
genNode size = do
  atom <- genAtom size
  frequency [(2, pure atom), (1, Repeat atom <$> genQuantifier)]

genAtom :: Int -> Gen Node
genAtom size = frequency $
  [ (3, Literal <$> elements subjectAlphabet)
  , (1, Class <$> (sublistOf "abc" `suchThat` (not . null)))
  ] <> [ (1, Group <$> genAlternatives (size `div` 2)) | size > 1 ]

genQuantifier :: Gen Quantifier
genQuantifier = oneof
  [ pure Optional
  , pure Star
  , pure Plus
  , Exactly <$> choose (1, 3)
  , AtLeast <$> choose (0, 3)
  , do
      low <- choose (0, 3)
      high <- choose (max 1 low, 4)
      pure (Between low high)
  ]

renderPattern :: Pattern -> String
renderPattern (Pattern alternatives) = "^" <> renderAlternatives alternatives <> "$"

renderAlternatives :: [[Node]] -> String
renderAlternatives = intercalate "|" . map (concatMap renderNode)

renderNode :: Node -> String
renderNode node = case node of
  Literal '.' -> "\\."
  Literal character -> [character]
  Class characters -> "[" <> characters <> "]"
  Group alternatives -> "(" <> renderAlternatives alternatives <> ")"
  Repeat atom quantifier -> renderNode atom <> renderQuantifier quantifier

renderQuantifier :: Quantifier -> String
renderQuantifier quantifier = case quantifier of
  Optional -> "?"
  Star -> "*"
  Plus -> "+"
  Exactly count -> "{" <> show count <> "}"
  AtLeast count -> "{" <> show count <> ",}"
  Between low high -> "{" <> show low <> "," <> show high <> "}"

-- | The reference: each node maps the set of positions it may start from to
-- the set it may end at; the whole subject matches when its length is
-- reachable from position zero.
referenceMatches :: Pattern -> String -> Bool
referenceMatches (Pattern alternatives) subject =
  length subject `Set.member` stepAlternatives subject alternatives (Set.singleton 0)

stepAlternatives :: String -> [[Node]] -> Set.Set Int -> Set.Set Int
stepAlternatives subject alternatives starts =
  Set.unions [foldl (flip (stepNode subject)) starts sequence' | sequence' <- alternatives]

stepNode :: String -> Node -> Set.Set Int -> Set.Set Int
stepNode subject node starts = case node of
  Literal character -> advance (== character)
  Class characters -> advance (`elem` characters)
  Group alternatives -> stepAlternatives subject alternatives starts
  Repeat atom quantifier ->
    let (low, high) = bounds quantifier
        step = stepNode subject atom
        required = iterate step starts !! low
    in case high of
         Just upper -> Set.unions (take (upper - low + 1) (iterate step required))
         Nothing -> closure step required
  where
    advance accepts = Set.fromList
      [ position + 1 | position <- Set.toList starts
      , position < length subject, accepts (subject !! position) ]

-- | Least fixed point of repeating a step; positions are bounded by the
-- subject's length, so it terminates.
closure :: (Set.Set Int -> Set.Set Int) -> Set.Set Int -> Set.Set Int
closure step reached =
  let next = reached `Set.union` step reached
  in if next == reached then reached else closure step next

bounds :: Quantifier -> (Int, Maybe Int)
bounds quantifier = case quantifier of
  Optional -> (0, Just 1)
  Star -> (0, Nothing)
  Plus -> (1, Nothing)
  Exactly count -> (count, Just count)
  AtLeast count -> (count, Nothing)
  Between low high -> (low, Just high)

-- | @{n}@, @{n,}@ and @{n,m}@ over a one-character atom accept a run of k
-- characters exactly when k is within the bounds, whatever the atom's form.
quantifierBounds :: Property
quantifierBounds =
  forAll (elements ["a", "[ab]", "(a|b)"]) $ \atom ->
  forAll genQuantifier $ \quantifier ->
  forAll (choose (0, 7)) $ \count ->
    let source = "^" <> atom <> renderQuantifier quantifier <> "$"
        (low, high) = bounds quantifier
        expected = count >= low && maybe True (count <=) high
    in case compilePattern (String (Text.pack source)) of
         Left problem -> counterexample (source <> ": " <> show problem) False
         Right compiled -> counterexample (source <> " against " <> show count <> " characters")
           (matchesPattern compiled (Text.replicate count "a") === expected)
