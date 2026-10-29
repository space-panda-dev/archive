{-# LANGUAGE OverloadedStrings #-}

-- | Who may assert a record under the 0.0.10 contracts (D-18): the kind of
-- Entity its agent is, what a supersession keeps, who may withdraw, a band's
-- adoption of a lead sheet, and a Work's writers and their consent.
--
-- It also holds ADR 0088's rule for the member contracts of earlier
-- releases, whose fixed bundles cannot carry it: a 0.0.10 record that
-- supersedes one of them keeps the agent of what it supersedes.
module Archive.Door.AgentRules
  ( validateAgentRules
  , validateMemberSupersession
  , memberContracts
  ) where

import Archive.Door.RuleJson
import Archive.Door.RuleRecords
import Archive.Door.RuleSyntax
import Archive.Door.Types (ContractCitation (..), DraftEnvelope (..), RecordBody (..), agentIdText)
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.List (nub, sort)
import Data.Text (Text)
import Data.Text qualified as Text

validateAgentRules :: RuleRecords -> DraftEnvelope -> KeyMap.KeyMap Value -> Either Text ()
validateAgentRules records draft schema = do
  let with name parse check = ruleAt name parse schema >>= maybe (Right ()) check
  with "x-archive-agent" agentRule (agentKind records draft)
  with "x-archive-capture-start" captureStartRule (const (captureAgent records draft))
  with "x-archive-operator-agent" operatorAgentRule (const (operatorAgent records draft))
  with "x-archive-supersession" supersessionRule (supersession records draft)
  with "x-archive-asserter-only" asserterOnlyRule (asserterOnly records draft)
  with "x-archive-adoption" adoptionRule (adoption records draft)
  with "x-archive-operator-writers" operatorWritersRule (operatorWriters records draft)
  with "x-archive-consenting-writer" consentingWriterRule (consentingWriter records draft)

agentOf :: DraftEnvelope -> Text
agentOf = agentIdText . envelopeAgent

-- | The kind of the Entity an agent names, read from its head revision; a
-- Package URL or an object that is not an Entity has none.
agentEntityKind :: RuleRecords -> Text -> Maybe Text
agentEntityKind records agent
  | not ("urn:uuid:" `Text.isPrefixOf` agent) = Nothing
  | otherwise = case envelopeBody <$> headRecord records agent of
      Just (EntityRecord (Object payload)) -> textAt "kind" payload
      _ -> Nothing

agentKind :: RuleRecords -> DraftEnvelope -> [Text] -> Either Text ()
agentKind records draft kinds
  | "software" `elem` kinds && "pkg:" `Text.isPrefixOf` agentOf draft = Right ()
  | otherwise = case agentEntityKind records (agentOf draft) of
      Just kind | kind `elem` kinds -> Right ()
      _ -> Left ("the record's agent must be an Archive object identifying a "
        <> Text.intercalate " or " kinds <> " Entity")

-- | A record that supersedes a revision keeps its agent, the objects it
-- names in @objects@, and the revisions it pins in @revisions@.
supersession :: RuleRecords -> DraftEnvelope -> SupersessionRule -> Either Text ()
supersession records draft rule = mapM_ check (envelopeSupersedes draft)
  where
    check ref = do
      previous <- pinnedRecord records ref
      if agentOf previous /= agentOf draft
        then Left "a record that supersedes a revision keeps its agent" else Right ()
      mapM_ (\role -> if objects role previous == objects role draft then Right ()
        else Left ("a record that supersedes a revision keeps its " <> role <> " object")) (supersessionObjects rule)
      mapM_ (\role -> if pins role previous == pins role draft then Right ()
        else Left ("a record that supersedes a revision keeps its " <> role <> " revision")) (supersessionRevisions rule)
    objects role = sort . map refObject . draftRefs role
    pins role = sort . map (\ref -> (refObject ref, refRevision ref)) . draftRefs role

-- | When the payload's field has the value, the agent is the agent of the
-- record pinned in the role: only the asserter takes a claim back.
asserterOnly :: RuleRecords -> DraftEnvelope -> NamedFields -> Either Text ()
asserterOnly records draft named
  | textAt (named "field") (draftPayload draft) /= Just (named "value") = Right ()
  | otherwise = do
      ref <- singleIn (named "role") draft
      target <- pinnedRecord records ref
      if agentOf target == agentOf draft then Right ()
        else Left ("a " <> named "value" <> " " <> named "field" <> " must be asserted by the agent of the record it is about")

-- | A person's lead sheet names no band and no adoption; a band's names the
-- band, which is its agent, and memberships of that band, no member twice.
adoption :: RuleRecords -> DraftEnvelope -> NamedFields -> Either Text ()
adoption records draft named = case agentEntityKind records agent of
  Just "person"
    | null bands && null adoptions -> Right ()
    | otherwise -> Left ("a person's record names no " <> named "band" <> " and no " <> named "adopted_by")
  Just "organisation" -> do
    band <- case bands of
      [band] | refObject band == agent -> Right band
      _ -> Left ("a band's record pins the band that is its agent in " <> named "band")
    if null adoptions then Left ("a band's record pins the memberships that adopted it in " <> named "adopted_by")
      else Right ()
    members <- concat <$> mapM (\ref -> do
      membership <- pinnedRecord records ref
      case draftRefs "band" membership of
        [owner] | refObject owner == refObject band -> Right ()
        _ -> Left (named "adopted_by" <> " must pin memberships of the record's band")
      pure (map refObject (draftRefs "member" membership))) adoptions
    if length members == length (nub members) then Right ()
      else Left (named "adopted_by" <> " must not name one member's adoption twice")
  _ -> Left "the record's agent must identify a person or organisation Entity"
  where
    agent = agentOf draft
    bands = draftRefs (named "band") draft
    adoptions = draftRefs (named "adopted_by") draft

-- | A Work's origin and its writers, against the active operator's person:
-- co-written names at least two writers, the operator among them; own names
-- no writer or only the operator; others never names the operator.
operatorWriters :: RuleRecords -> DraftEnvelope -> NamedFields -> Either Text ()
operatorWriters records draft named = do
  operator <- activeOperator records
  let writers = map refObject (draftRefs (named "role") draft)
  case textAt (named "field") (draftPayload draft) of
    Just "co_written" | length writers >= 2 && operator `elem` writers -> Right ()
                      | otherwise -> Left "a co-written Work names at least two writers, the operator among them"
    Just "own" | writers `elem` [[], [operator]] -> Right ()
               | otherwise -> Left "the operator's own Work names no writer but the operator"
    Just "others" | operator `notElem` writers -> Right ()
                  | otherwise -> Left "another's Work never names the operator as a writer"
    _ -> Right ()

-- | A consent's agent is one of the writers its origin names, never the
-- active operator's person.
consentingWriter :: RuleRecords -> DraftEnvelope -> NamedFields -> Either Text ()
consentingWriter records draft named = do
  operator <- activeOperator records
  ref <- singleIn (named "role") draft
  origin <- pinnedRecord records ref
  let agent = agentOf draft
  if agent == operator then Left "a sharing consent is never the operator's" else Right ()
  if agent `elem` map refObject (draftRefs (named "writers") origin) then Right ()
    else Left ("a consent must be asserted by a writer its " <> named "role" <> " names")

-- | The member contracts of ADR 0088 (World's list in @world/circle.py@).
memberContracts :: [(Text, Text)]
memberContracts =
  [ ("archive.assessment/judgment", "1"), ("archive.assessment/judgment", "2")
  , ("archive.claim/span_annotation", "1")
  , ("archive.claim/chord_stacks", "1"), ("archive.claim/chord_stacks", "2")
  , ("archive.claim/chord_sequence", "1"), ("archive.claim/chord_sequence", "2")
  , ("archive.claim/meter_layers", "1"), ("archive.claim/meter_layers", "2")
  , ("archive.claim/key_regions", "1"), ("archive.claim/key_sequence", "1"), ("archive.claim/tuning", "1")
  , ("archive.claim/form", "1"), ("archive.claim/form_spans", "1"), ("archive.claim/lead_sheet", "1")
  , ("archive.claim/timed_lyrics", "1"), ("archive.claim/notes", "1"), ("archive.claim/melody", "1")
  , ("archive.claim/melody_spans", "1")
  , ("archive.claim/contribution", "1"), ("archive.claim/band_membership", "1")
  , ("archive.claim/selection_kind", "1"), ("archive.claim/selection_title", "1")
  , ("archive.claim/performance_work", "3")
  , ("archive.claim/sharing_consent", "1")
    -- ADR 0113 (specification 0.0.11), as its "Who may save" and ADR 0094's
    -- amendment list them; meter layers version 3 is not among them
  , ("archive.claim/melody", "2"), ("archive.claim/underlay", "1"), ("archive.claim/form", "2")
  , ("archive.claim/form_spans", "2"), ("archive.claim/lead_sheet", "2")
  ]

-- | A record of specification 0.0.10 or later that cites a member contract
-- and supersedes a revision keeps that revision's agent (ADR 0088, D-18).
validateMemberSupersession :: RuleRecords -> DraftEnvelope -> Either Text ()
validateMemberSupersession records draft
  | envelopeSpec draft `elem` earlier = Right ()
  | not (any (\citation -> (contractId citation, contractVersion citation) `elem` memberContracts)
          (envelopeContracts draft)) = Right ()
  | otherwise = mapM_ (\ref -> do
      previous <- pinnedRecord records ref
      if agentOf previous == agentOf draft then Right ()
        else Left "a record under a member contract that supersedes a revision keeps its agent") (envelopeSupersedes draft)
  where
    earlier = ["archive/0.0." <> Text.pack (show minor) | minor <- [1 .. 9 :: Int]]

captureAgent :: RuleRecords -> DraftEnvelope -> Either Text ()
captureAgent records draft
  | textAt "basis" (draftPayload draft) `elem` [Just "person", Just "client"] = agentKind records draft ["person"]
  | otherwise = Right ()

operatorAgent :: RuleRecords -> DraftEnvelope -> Either Text ()
operatorAgent records draft = do
  operator <- activeOperator records
  if agentOf draft == operator then Right ()
    else Left "this claim must be asserted by the active operator"
