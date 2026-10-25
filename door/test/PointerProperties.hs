{-# LANGUAGE OverloadedStrings #-}

-- | Properties of JSON Pointers ("Archive.Door.Contracts.Pointer"), and RFC
-- 6901 §5's example pointers as fixed cases.
--
-- 'instancePointer' writes the place a refusal names and 'resolvePointer'
-- follows a schema's local @$ref@; they escape and unescape separately, so
-- the round trip through both is what keeps a key holding @~@ or @/@ from
-- naming a different place than the one it came from.
module PointerProperties (pointerProperties) where

import Archive.Door.Contracts.Pointer (instancePointer, resolvePointer)
import Data.Aeson (Value (..))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import Data.Text (Text)
import qualified Data.Text as Text
import qualified Data.Vector as Vector
import Test.Tasty (TestTree, testGroup)
import Test.Tasty.HUnit (testCase, (@?=))
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
  , testProperty
  , vectorOf
  , (===)
  )

pointerProperties :: TestTree
pointerProperties = testGroup "pointerProperties"
  [ testProperty "a key survives escaping and unescaping" escapeRoundTrip
  , testProperty "a generated path resolves to the value at it" pathResolves
  , testProperty "an index past the end resolves to nothing" indexPastEnd
  , rfc6901Examples
  ]

-- | RFC 6901 §5's document and each of its example pointers, written as the
-- local @$ref@ fragments a schema holds (@#@ before the JSON string form),
-- plus @~01@, the spelling whose order of unescaping §4 settles: it names
-- the key @~1@, never @/@.
rfc6901Examples :: TestTree
rfc6901Examples = testGroup "RFC 6901 section 5 examples"
  [ testCase (show pointer) (resolvePointer document pointer @?= expected)
  | (pointer, expected) <-
      [ ("#", Just document)
      , ("#/foo", Just (Array (Vector.fromList [String "bar", String "baz"])))
      , ("#/foo/0", Just (String "bar"))
      , ("#/", Just (number 0))
      , ("#/a~1b", Just (number 1))
      , ("#/c%d", Just (number 2))
      , ("#/e^f", Just (number 3))
      , ("#/g|h", Just (number 4))
      , ("#/i\\j", Just (number 5))
      , ("#/k\"l", Just (number 6))
      , ("#/ ", Just (number 7))
      , ("#/m~0n", Just (number 8))
      , ("#/~01", Just (number 9))
      , ("#/~1", Just (number 10))
      ]
  ]
  where
    number :: Int -> Value
    number = Number . fromIntegral
    document = Object (KeyMap.fromList
      [ ("foo", Array (Vector.fromList [String "bar", String "baz"]))
      , ("", number 0)
      , ("a/b", number 1)
      , ("c%d", number 2)
      , ("e^f", number 3)
      , ("g|h", number 4)
      , ("i\\j", number 5)
      , ("k\"l", number 6)
      , (" ", number 7)
      , ("m~n", number 8)
      , ("~1", number 9)
      , ("/", number 10)
      ])

-- | Keys weighted toward the two escaped characters and their escapes, so
-- spellings such as @~0@, @~1@ and @~/@ occur often; a key holding @~1@
-- escapes to @~01@, the spelling RFC 6901 §4's order of unescaping settles.
genKey :: Gen Text
genKey = Text.concat <$> resize 6 (listOf (elements ["a", "0", "~", "/", "~0", "~1", "01", "é", ""]))

genLeaf :: Gen Value
genLeaf = oneof
  [ pure Null
  , Bool <$> elements [False, True]
  , Number . fromIntegral <$> choose (-1000, 1000 :: Int)
  , String <$> genKey
  ]

-- | Resolving @#@ plus the pointer for a single key into an object holding
-- that key gives back its value.
escapeRoundTrip :: Property
escapeRoundTrip = forAll genKey $ \key -> forAll genLeaf $ \leaf ->
  let pointer = "#" <> instancePointer [key]
      container = Object (KeyMap.fromList [(Key.fromText key, leaf), ("other", Null)])
  in counterexample (Text.unpack pointer) (resolvePointer container pointer === Just leaf)

-- | A value with a path into it: each step is a key of an object or an index
-- of an array, with siblings around it so the step must pick the right one.
data Located = Located Value [Text] Value
  deriving (Show)

genLocated :: Gen Located
genLocated = sized $ \size -> do
  depth <- choose (0, min 4 size)
  target <- genLeaf
  wrap depth target
  where
    wrap :: Int -> Value -> Gen Located
    wrap 0 target = pure (Located target [] target)
    wrap depth target = do
      Located inner path found <- wrap (depth - 1) target
      frequency
        [ (1, do
            key <- genKey
            siblings <- resize 3 (listOf ((,) <$> genKey <*> genLeaf))
            let fields = KeyMap.insert (Key.fromText key) inner
                  (KeyMap.fromList [(Key.fromText name, value) | (name, value) <- siblings])
            pure (Located (Object fields) (key : path) found))
        , (1, do
            before <- choose (0, 3) >>= (`vectorOf` genLeaf)
            after <- choose (0, 2) >>= (`vectorOf` genLeaf)
            let index = Text.pack (show (length before))
            pure (Located (Array (Vector.fromList (before <> [inner] <> after))) (index : path) found))
        ]

pathResolves :: Property
pathResolves = forAll genLocated $ \(Located root path found) ->
  let pointer = if null path then "#" else "#" <> instancePointer (reverse path)
  in counterexample (Text.unpack pointer) (resolvePointer root pointer === Just found)

indexPastEnd :: Property
indexPastEnd = forAll (choose (0, 4) >>= (`vectorOf` genLeaf)) $ \values ->
  forAll (choose (0, 3)) $ \beyond ->
    let root = Array (Vector.fromList values)
        index = length values + beyond
    in resolvePointer root ("#/" <> Text.pack (show index)) === Nothing
