{-# LANGUAGE OverloadedStrings #-}

-- | Profile rules specification 0.0.10 declares that this Door does not yet
-- enforce.
--
-- The 0.0.10 bundles are fixed bytes: an archive copies them at
-- initialization or upgrade, and their digests are cited for good. Their
-- profile rules are therefore written into the bundles now, with the
-- release, rather than added later under new digests. A rule here is known
-- by name, so a bundle declaring it loads, initializes, and upgrades; but a
-- record citing such a bundle is refused, because Door cannot check what the
-- rule says. Unimplemented behaviour fails loudly (AGENTS.md): no record is
-- admitted on the strength of a rule nobody evaluated.
--
-- Enforcing a rule removes its name from this list and adds its parser and
-- evaluator beside the earlier rules. The specification (D-18) states each
-- rule's meaning.
module Archive.Door.PendingRules
  ( pendingProfileRuleNames
  , refusePendingRules
  ) where

import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Text (Text)
import Data.Text qualified as Text

pendingProfileRuleNames :: [Text]
pendingProfileRuleNames =
  [ "x-archive-distinct-revisions"
  , "x-archive-when-pinned"
  , "x-archive-target-form"
  , "x-archive-written-bars"
  , "x-archive-spellings"
  , "x-archive-ordered-ranges"
  , "x-archive-ordered-fields"
  , "x-archive-pinned-subjects"
  , "x-archive-judged-scope"
  , "x-archive-asserter-only"
  , "x-archive-agent"
  , "x-archive-target-span"
  , "x-archive-mark-about"
  , "x-archive-form-structure"
  , "x-archive-adoption"
  , "x-archive-target-values"
  , "x-archive-ref-index"
  , "x-archive-distinct-values"
  , "x-archive-supersession"
  , "x-archive-file-names"
  , "x-archive-ascending-tuples"
  , "x-archive-contained-spans"
  , "x-archive-within-range"
  , "x-archive-named-refs"
  , "x-archive-ref-per-item"
  , "x-archive-tracklist-positions"
  , "x-archive-tracklist-entry"
  , "x-archive-operator-writers"
  , "x-archive-consenting-writer"
  , "x-archive-export-key"
  ]

-- | Refuse a record whose contract schema declares a rule this Door does not
-- enforce yet, naming the contract and the rules.
refusePendingRules :: Text -> Text -> Value -> Either Text ()
refusePendingRules identifier version (Object schema) =
  case filter (\name -> KeyMap.member (Key.fromText name) schema) pendingProfileRuleNames of
    [] -> Right ()
    names -> Left ("unsupported: " <> identifier <> " version " <> version
      <> " declares profile rules this Door does not enforce yet: " <> Text.intercalate ", " names)
refusePendingRules _ _ _ = Left "contract schema must be an object"
