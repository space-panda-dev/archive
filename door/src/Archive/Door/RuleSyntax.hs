{-# LANGUAGE OverloadedStrings #-}

-- | The profile rules specification 0.0.10 adds (D-18), as Door parses them
-- when a bundle loads.
--
-- Every rule here is closed and root-only, names distinct fields, and is
-- refused when unknown or malformed. The evaluators parse a rule through the
-- same functions, so a rule that loads is the rule that is enforced.
module Archive.Door.RuleSyntax
  ( releaseRuleNames
  , validateReleaseRules
  , ruleAt
    -- * Typed rules
  , OrderedField (..)
  , orderedFieldsRule
  , orderedRangesRule
  , AscendingTuples (..)
  , ascendingTuplesRule
  , containedSpansRule
  , distinctValuesRule
  , fileNamesRule
  , SpellingsRule (..)
  , spellingsRule
  , WhenPinnedRule (..)
  , whenPinnedRule
  , writtenBarsRule
  , TargetFormRule (..)
  , targetFormRule
  , PinnedSubjects (..)
  , pinnedSubjectsRule
  , TargetValues (..)
  , targetValuesRule
  , targetSpanRule
  , withinRangeRule
  , RefPathRule (..)
  , refPathRule
  , agentRule
  , SupersessionRule (..)
  , supersessionRule
  , asserterOnlyRule
  , roleRule
  , formStructureRule
  , adoptionRule
  , tracklistPositionsRule
  , tracklistEntryRule
  , operatorWritersRule
  , consentingWriterRule
  , exportKeyRule
  , distinctRevisionsRule
  ) where

import Archive.Door.RuleJson
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.List (nub)
import Data.Text (Text)
import Data.Text qualified as Text

-- | Every rule name the release adds, in D-18's order.
releaseRuleNames :: [Text]
releaseRuleNames = map fst parsers <> ["x-archive-when-pinned"]

parsers :: [(Text, Value -> Either Text ())]
parsers =
  [ ("x-archive-distinct-revisions", () <$$ distinctRevisionsRule)
  , ("x-archive-ordered-fields", () <$$ orderedFieldsRule)
  , ("x-archive-ordered-ranges", () <$$ orderedRangesRule)
  , ("x-archive-ascending-tuples", () <$$ ascendingTuplesRule)
  , ("x-archive-contained-spans", () <$$ containedSpansRule)
  , ("x-archive-distinct-values", () <$$ distinctValuesRule)
  , ("x-archive-file-names", () <$$ fileNamesRule)
  , ("x-archive-spellings", () <$$ spellingsRule)
  , ("x-archive-written-bars", () <$$ writtenBarsRule)
  , ("x-archive-target-form", () <$$ targetFormRule)
  , ("x-archive-pinned-subjects", () <$$ pinnedSubjectsRule)
  , ("x-archive-target-values", () <$$ targetValuesRule)
  , ("x-archive-target-span", () <$$ targetSpanRule)
  , ("x-archive-within-range", () <$$ withinRangeRule)
  , ("x-archive-ref-index", () <$$ refPathRule "ref index rule")
  , ("x-archive-ref-per-item", () <$$ refPathRule "ref per item rule")
  , ("x-archive-named-refs", () <$$ refPathRule "named refs rule")
  , ("x-archive-agent", () <$$ agentRule)
  , ("x-archive-supersession", () <$$ supersessionRule)
  , ("x-archive-asserter-only", () <$$ asserterOnlyRule)
  , ("x-archive-judged-scope", () <$$ roleRule "judged scope rule")
  , ("x-archive-mark-about", () <$$ roleRule "mark about rule")
  , ("x-archive-form-structure", () <$$ formStructureRule)
  , ("x-archive-adoption", () <$$ adoptionRule)
  , ("x-archive-tracklist-positions", () <$$ tracklistPositionsRule)
  , ("x-archive-tracklist-entry", () <$$ tracklistEntryRule)
  , ("x-archive-operator-writers", () <$$ operatorWritersRule)
  , ("x-archive-consenting-writer", () <$$ consentingWriterRule)
  , ("x-archive-export-key", () <$$ exportKeyRule)
  ]
  where
    (<$$) unit parse value = unit <$ parse value

-- | Parse every 0.0.10 rule at a schema's root. @nested@ checks the schema
-- each of @x-archive-when-pinned@'s maps makes, the root without the rule and
-- with the map's rules (the caller owns the earlier rules' parsers).
validateReleaseRules :: (KeyMap.KeyMap Value -> Either Text ()) -> KeyMap.KeyMap Value -> Either Text ()
validateReleaseRules nested schema = do
  mapM_ (\(name, parse) -> maybe (Right ()) parse (KeyMap.lookup (Key.fromText name) schema)) parsers
  case KeyMap.lookup "x-archive-when-pinned" schema of
    Nothing -> Right ()
    Just value -> do
      rule <- whenPinnedRule value
      mapM_ (\rules -> do
        let names = map Key.toText (KeyMap.keys rules)
        if any (`KeyMap.member` schema) (map Key.fromText names)
          then Left "a rule in when pinned rule must not also appear at the schema root"
          else Right ()
        if "x-archive-when-pinned" `elem` names
          then Left "when pinned rules must not nest"
          else Right ()
        if all ("x-archive-" `Text.isPrefixOf`) names
          then Right ()
          else Left "when pinned rule maps may hold only profile rules"
        nested (KeyMap.union rules (KeyMap.delete "x-archive-when-pinned" schema)))
        [whenPinnedThen rule, whenPinnedElse rule]

-- | A rule's value at the schema root, parsed.
ruleAt :: Text -> (Value -> Either Text a) -> KeyMap.KeyMap Value -> Either Text (Maybe a)
ruleAt name parse schema = traverse parse (KeyMap.lookup (Key.fromText name) schema)

-- ---------------------------------------------------------------------------
-- Rules reading one record

distinctRevisionsRule :: Value -> Either Text [Text]
distinctRevisionsRule value = do
  fields <- ruleObject label value
  exactKeys label ["roles"] fields
  textsField label "roles" fields
  where label = "distinct revisions rule"

data OrderedField = OrderedField
  { orderedPath :: [Text], orderedLow :: Text, orderedHigh :: Text, orderedStrict :: Bool }

orderedFieldsRule :: Value -> Either Text [OrderedField]
orderedFieldsRule = ruleList label $ \fields -> do
  exactKeys label ["path", "low", "high", "strict"] fields
  rule <- OrderedField <$> pathField label "path" fields <*> textField label "low" fields
    <*> textField label "high" fields <*> boolField label "strict" fields
  if orderedLow rule == orderedHigh rule then Left (label <> " must name two fields") else Right rule
  where label = "ordered fields rule"

orderedRangesRule :: Value -> Either Text NamedFields
orderedRangesRule = namedFields "ordered ranges rule" ["payload", "first", "last"]

data AscendingTuples = AscendingTuples
  { tuplesPath :: [Text], tuplesFields :: [Text], tuplesStrict :: Bool }

ascendingTuplesRule :: Value -> Either Text [AscendingTuples]
ascendingTuplesRule = ruleList label $ \fields -> do
  exactKeys label ["payload", "fields", "strict"] fields
  AscendingTuples <$> pathField label "payload" fields <*> textsField label "fields" fields
    <*> boolField label "strict" fields
  where label = "ascending tuples rule"

containedSpansRule :: Value -> Either Text NamedFields
containedSpansRule = namedFields "contained spans rule" ["outer", "inner", "start", "end"]

distinctValuesRule :: Value -> Either Text [[Text]]
distinctValuesRule value = do
  fields <- ruleObject label value
  exactKeys label ["paths"] fields
  case KeyMap.lookup "paths" fields of
    Just (Array items) | not (null items) -> do
      paths <- mapM (\item -> pathField label "path" (KeyMap.singleton "path" item)) (foldr (:) [] items)
      if length paths == length (nub paths) then Right paths else Left (label <> " must not repeat a path")
    _ -> Left (label <> " paths must be a non-empty array of paths")
  where label = "distinct values rule"

fileNamesRule :: Value -> Either Text [Text]
fileNamesRule value = do
  fields <- ruleObject label value
  exactKeys label ["fields"] fields
  textsField label "fields" fields
  where label = "file names rule"

data SpellingsRule = SpellingsRule
  { spellingsPath :: [Text], spellingsClasses :: Text, spellingsNames :: Text }

spellingsRule :: Value -> Either Text SpellingsRule
spellingsRule value = do
  fields <- ruleObject label value
  exactKeys label ["payload", "classes", "names"] fields
  rule <- SpellingsRule <$> pathField label "payload" fields <*> textField label "classes" fields
    <*> textField label "names" fields
  if spellingsClasses rule == spellingsNames rule then Left (label <> " must name two fields") else Right rule
  where label = "spellings rule"

-- | @x-archive-when-pinned@: two maps of earlier rules, chosen by whether
-- the record pins a reference in @role@.
data WhenPinnedRule = WhenPinnedRule
  { whenPinnedRole :: Text
  , whenPinnedThen :: KeyMap.KeyMap Value
  , whenPinnedElse :: KeyMap.KeyMap Value
  }

whenPinnedRule :: Value -> Either Text WhenPinnedRule
whenPinnedRule value = do
  fields <- ruleObject label value
  exactKeys label ["role", "then", "else"] fields
  WhenPinnedRule <$> textField label "role" fields <*> rules "then" fields <*> rules "else" fields
  where
    label = "when pinned rule"
    rules name fields = case KeyMap.lookup name fields of
      Just (Object map') | not (KeyMap.null map') -> Right map'
      _ -> Left (label <> " " <> Key.toText name <> " must be a non-empty map of rules")

writtenBarsRule :: Value -> Either Text NamedFields
writtenBarsRule = namedFields "written bars rule" ["layers", "bars", "modulations"]

formStructureRule :: Value -> Either Text NamedFields
formStructureRule = namedFields "form structure rule" ["sections", "repeats", "jumps"]

tracklistPositionsRule :: Value -> Either Text NamedFields
tracklistPositionsRule = namedFields "tracklist positions rule" ["media", "tracks", "position", "partial"]

data RefPathRule = RefPathRule { refPath :: [Text], refRole :: Text }

refPathRule :: Text -> Value -> Either Text RefPathRule
refPathRule label value = do
  fields <- ruleObject label value
  exactKeys label ["path", "role"] fields
  RefPathRule <$> pathField label "path" fields <*> textField label "role" fields

exportKeyRule :: Value -> Either Text NamedFields
exportKeyRule = namedFields "export key rule" ["key", "rendered", "joined"]

-- ---------------------------------------------------------------------------
-- Rules reading pinned records

-- | @x-archive-target-form@: per role, whether a pinned Claim pins nothing
-- in @by@ (@without@, False) or pins it exactly when this record does
-- (@same@, True).
data TargetFormRule = TargetFormRule { targetFormBy :: Text, targetFormRoles :: [(Text, Bool)] }

targetFormRule :: Value -> Either Text TargetFormRule
targetFormRule value = do
  fields <- ruleObject label value
  exactKeys label ["by", "roles"] fields
  by <- textField label "by" fields
  roles <- case KeyMap.lookup "roles" fields of
    Just (Object roles) | not (KeyMap.null roles) -> mapM mode (KeyMap.toList roles)
    _ -> Left (label <> " roles must be a non-empty map")
  if by `elem` map fst roles then Left (label <> " must not name its by role among its roles") else Right ()
  pure (TargetFormRule by roles)
  where
    label = "target form rule"
    mode (role, String "without") | not (Key.toText role == "") = Right (Key.toText role, False)
    mode (role, String "same") | not (Key.toText role == "") = Right (Key.toText role, True)
    mode _ = Left (label <> " roles must map a role to without or same")

data PinnedSubjects = PinnedSubjects
  { pinnedRoles :: [Text], pinnedSubject :: Text, pinnedEquals :: Text, pinnedRevision :: Bool }

pinnedSubjectsRule :: Value -> Either Text [PinnedSubjects]
pinnedSubjectsRule = ruleList label $ \fields -> do
  exactKeys label ["roles", "subject", "equals", "match"] fields
  revision <- case KeyMap.lookup "match" fields of
    Just (String "object") -> Right False
    Just (String "revision") -> Right True
    _ -> Left (label <> " match must be object or revision")
  PinnedSubjects <$> textsField label "roles" fields <*> textField label "subject" fields
    <*> textField label "equals" fields <*> pure revision
  where label = "pinned subjects rule"

data TargetValues = TargetValues { targetValuesRole :: Text, targetValuesField :: Text, targetValuesValues :: [Text] }

targetValuesRule :: Value -> Either Text [TargetValues]
targetValuesRule = ruleList label $ \fields -> do
  exactKeys label ["role", "field", "values"] fields
  TargetValues <$> textField label "role" fields <*> textField label "field" fields
    <*> textsField label "values" fields
  where label = "target values rule"

targetSpanRule :: Value -> Either Text NamedFields
targetSpanRule = namedFields "target span rule" ["role", "rate", "count", "span", "start", "end"]

withinRangeRule :: Value -> Either Text NamedFields
withinRangeRule = namedFields "within range rule" ["role", "count", "range", "start", "end", "items", "field"]

agentRule :: Value -> Either Text [Text]
agentRule value = do
  fields <- ruleObject label value
  exactKeys label ["kinds"] fields
  textsField label "kinds" fields
  where label = "agent rule"

-- | @x-archive-supersession@: a superseding record keeps its agent (the rule
-- names @agent@ true), the objects in @objects@, and the revisions in
-- @revisions@.
data SupersessionRule = SupersessionRule { supersessionObjects :: [Text], supersessionRevisions :: [Text] }

supersessionRule :: Value -> Either Text SupersessionRule
supersessionRule value = do
  fields <- ruleObject label value
  exactKeys label ["agent", "objects", "revisions"] fields
  case KeyMap.lookup "agent" fields of
    Just (Bool True) -> Right ()
    _ -> Left (label <> " agent must be true")
  rule <- SupersessionRule <$> textsField label "objects" fields <*> textsField label "revisions" fields
  if any (`elem` supersessionRevisions rule) (supersessionObjects rule)
    then Left (label <> " must not name a role in both objects and revisions")
    else Right rule
  where label = "supersession rule"

asserterOnlyRule :: Value -> Either Text NamedFields
asserterOnlyRule = namedFields "asserter only rule" ["field", "value", "role"]

roleRule :: Text -> Value -> Either Text Text
roleRule label value = do
  fields <- ruleObject label value
  exactKeys label ["role"] fields
  textField label "role" fields

adoptionRule :: Value -> Either Text NamedFields
adoptionRule = namedFields "adoption rule" ["band", "adopted_by"]

tracklistEntryRule :: Value -> Either Text NamedFields
tracklistEntryRule = namedFields "tracklist entry rule" ["role", "medium", "track", "recording"]

operatorWritersRule :: Value -> Either Text NamedFields
operatorWritersRule = namedFields "operator writers rule" ["field", "role"]

consentingWriterRule :: Value -> Either Text NamedFields
consentingWriterRule = namedFields "consenting writer rule" ["role", "writers"]
