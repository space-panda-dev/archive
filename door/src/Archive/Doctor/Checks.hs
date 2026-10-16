{-# LANGUAGE OverloadedStrings #-}

-- | Whole-archive checks that do not need to know where records are stored.
--
-- This is deliberately a read-only, pure seam.  The caller supplies the
-- records it was able to read; the report keeps unresolved and unsupported
-- work visible instead of turning it into an empty collection (INV-24).
module Archive.Doctor.Checks
  ( CheckStatus (..)
  , ContractKey (..)
  , DoctorConfig (..)
  , DoctorCoverage (..)
  , DoctorIssue (..)
  , IssueCode (..)
  , DoctorReport (..)
  , doctor
  , doctorWithConfig
  ) where

import Archive.Door.Layout (parseSha256Digest)
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (..)
  , ObjectId
  , objectIdText
  , RecordBody (..)
  , Reference (..)
  , RevisionId, revisionIdText
  , Shape (..)
  , StoredEnvelope (..)
  )
import qualified Data.Map.Strict as Map
import Data.Maybe (mapMaybe)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

-- | A complete contract citation, suitable for comparison with the set of
-- archive-resident contract bundles.  Schema evaluation is intentionally not
-- hidden in this module; callers get an explicit incomplete status for it.
data ContractKey = ContractKey
  { contractKeyId :: Text
  , contractKeyVersion :: Text
  , contractKeyDigest :: Text
  } deriving (Eq, Ord, Show)

data DoctorConfig = DoctorConfig
  { -- | Keys found in the archive's installed contract directory.  An empty
    -- set means that the caller did not provide a catalog, not that no
    -- contracts are installed.
    installedContracts :: Maybe (Set.Set ContractKey)
  , -- | True only when the reader has already validated every cited bundle's
    -- schema and reference-role rules.  Catalog membership alone is not
    -- schema validation.
    contractSchemasValidated :: Bool
  } deriving (Eq, Show)

data CheckStatus
  = CheckPassed
  | CheckNotRun Text
  | CheckIncomplete Text
  deriving (Eq, Show)

data IssueCode
  = DuplicateRevision
  | DuplicateObjectRevision
  | DanglingReference
  | WrongRevisionPin
  | UnpinnedSupersession
  | InvalidSupersessionRole
  | SupersessionWrongObject
  | SupersessionWrongShape
  | SupersessionFork
  | SupersessionCycle
  | MissingAssessmentTarget
  | UnpinnedAssessmentTarget
  | AssessmentTargetWrongShape
  | MissingContractCitation
  | InvalidContractCitation
  | ContractNotInstalled
  deriving (Eq, Ord, Show)

data DoctorIssue = DoctorIssue
  { issueCode :: IssueCode
  , issueRecord :: Maybe RevisionId
  , issueObject :: Maybe ObjectId
  , issueDetail :: Text
  } deriving (Eq, Show)

-- | Counts are reported even when the archive is invalid or incomplete.  In
-- particular, an empty count is not used to claim that unreadable records do
-- not exist; the reader owns the distinction between an empty input and a
-- failed read.
data DoctorCoverage = DoctorCoverage
  { coverageRecords :: Int
  , coverageObjects :: Int
  , coverageArtifacts :: Int
  , coverageEntities :: Int
  , coverageClaims :: Int
  , coverageAssessments :: Int
  , coverageReferences :: Int
  , coveragePinnedReferences :: Int
  , coverageUnpinnedReferences :: Int
  , coverageSupersessions :: Int
  , coverageAssessmentTargets :: Int
  , coverageContractCitations :: Int
  } deriving (Eq, Show)

data DoctorReport = DoctorReport
  { doctorIssues :: [DoctorIssue]
  , doctorCoverage :: DoctorCoverage
  , doctorContractCheck :: CheckStatus
  , doctorCurrentCheck :: CheckStatus
  } deriving (Eq, Show)

-- | Run the structural checks without pretending that a contract catalog was
-- read.  The returned report therefore says explicitly that contract-schema
-- and current-rule checks were not run.
doctor :: [StoredEnvelope] -> DoctorReport
doctor = doctorWithConfig (DoctorConfig Nothing False)

-- | Run checks using records already decoded by the reader.  The function
-- checks references and lineage independent of filesystem layout.  The reader
-- may mark contract validation complete only after it has checked schemas and
-- reference roles; this module never infers that fact from filenames or
-- catalog membership.  Per-kind current rules remain an explicit boundary
-- (INV-24, S-12, S-18).
doctorWithConfig :: DoctorConfig -> [StoredEnvelope] -> DoctorReport
doctorWithConfig config records =
  let identities = identityIndex records
      issues = concat
        [ duplicateIssues identities
        , referenceIssues identities records
        , supersessionIssues identities records
        , assessmentIssues identities records
        , contractIssues config records
        ]
      contractStatus = contractStatusFor config
  in DoctorReport
       { doctorIssues = sortIssues issues
       , doctorCoverage = coverage records
       , doctorContractCheck = contractStatus
       , doctorCurrentCheck = CheckNotRun
           "no current rule is defined"
       }

data IdentityIndex = IdentityIndex
  { byRevision :: Map.Map RevisionId [StoredEnvelope]
  , byObject :: Map.Map ObjectId [StoredEnvelope]
  } deriving (Eq, Show)

identityIndex :: [StoredEnvelope] -> IdentityIndex
identityIndex = foldl' insertRecord (IdentityIndex Map.empty Map.empty)
  where
    insertRecord index record =
      let revision = storedRevision record
          objectId = envelopeObject (storedDraft record)
      in index
           { byRevision = Map.insertWith (<>) revision [record] (byRevision index)
           , byObject = Map.insertWith (<>) objectId [record] (byObject index)
           }

duplicateIssues :: IdentityIndex -> [DoctorIssue]
duplicateIssues index =
  duplicateRevisionIssues <> duplicateObjectIssues
  where
    duplicateRevisionIssues =
      [ issue DuplicateRevision record Nothing
          ("revision_id occurs for multiple objects (" <> decimal (length values)
            <> " records)")
      | values <- Map.elems (byRevision index)
      , Set.size (Set.fromList (map (envelopeObject . storedDraft) values)) > 1
      , record <- take 1 values
      ]
    duplicateObjectIssues =
      [ issue DuplicateObjectRevision record (Just objectId)
          "the same object and revision occur more than once"
      | (objectId, values) <- Map.toList (byObject index)
      , (_revision, grouped) <- groupedByRevision values
      , length grouped > 1
      , record <- take 1 grouped
      ]
      where
        groupedByRevision values =
          Map.toList (Map.fromListWith (<>)
            [ (storedRevision value, [value]) | value <- values ])

referenceIssues :: IdentityIndex -> [StoredEnvelope] -> [DoctorIssue]
referenceIssues index records = concatMap checkRecord records
  where
    checkRecord record = concatMap (checkReference record) allReferences
      where
        draft = storedDraft record
        allReferences = envelopeReferences draft <> envelopeSupersedes draft

    checkReference record reference =
      case Map.lookup (referenceObject reference) (byObject index) of
        Nothing -> [issue DanglingReference record (Just (referenceObject reference))
          ("reference names no admitted object: " <> objectText (referenceObject reference))]
        Just objectRecords -> case referenceRevision reference of
          Nothing -> []
          Just revision ->
            let sameObject = any ((== revision) . storedRevision) objectRecords
                elsewhere = Map.member revision (byRevision index)
            in if sameObject
                 then []
                 else [issue WrongRevisionPin record (Just (referenceObject reference))
                         (if elsewhere
                            then "revision belongs to another object: " <> revisionText revision
                            else "revision is not admitted for this object: " <> revisionText revision)]

supersessionIssues :: IdentityIndex -> [StoredEnvelope] -> [DoctorIssue]
supersessionIssues index records =
  concatMap checkRecord records <> forkIssues <> cycleIssues
  where
    checkRecord record = concatMap checkSupersession (envelopeSupersedes draft)
      where
        draft = storedDraft record
        shape = bodyShape (envelopeBody draft)
        checkSupersession reference
          | referenceRole reference /= "supersedes" =
              [issue InvalidSupersessionRole record (Just (envelopeObject draft))
                "supersedes array contains a reference whose role is not supersedes"]
          | referenceRevision reference == Nothing =
              [issue UnpinnedSupersession record (Just (envelopeObject draft))
                "a supersession must pin an exact revision"]
          | referenceObject reference /= envelopeObject draft =
              [issue SupersessionWrongObject record (Just (envelopeObject draft))
                "a supersession must name the same object"]
          | otherwise = case lookupRevision index reference of
              Nothing -> [] -- reported once by referenceIssues
              Just target ->
                [ issue SupersessionWrongShape record (Just (envelopeObject draft))
                    "a supersession must retain the record shape"
                | bodyShape (envelopeBody (storedDraft target)) /= shape
                ]

    -- An edge is newer -> older.  Two newer records pointing at the same
    -- older record are a fork even though both edges individually resolve.
    edges =
      [ (storedRevision record, revision)
      | record <- records
      , reference <- envelopeSupersedes (storedDraft record)
      , Just revision <- [referenceRevision reference]
      , referenceRole reference == "supersedes"
      , referenceObject reference == envelopeObject (storedDraft record)
      ]
    forkIssues =
      [ issueForRevision SupersessionFork (Just target) Nothing
          ("multiple revisions supersede " <> revisionText target)
      | (target, predecessors) <- Map.toList (Map.fromListWith Set.union
          [ (older, Set.singleton newer) | (newer, older) <- edges ])
      , Set.size predecessors > 1
      ]
    cycleIssues = map cycleIssue (findCycles edges)
    cycleIssue cycleNodes = issueForRevision SupersessionCycle (headMay cycleNodes) Nothing
      ("supersession cycle: " <> Text.intercalate " -> " (map revisionText cycleNodes))

assessmentIssues :: IdentityIndex -> [StoredEnvelope] -> [DoctorIssue]
assessmentIssues index records = concatMap check records
  where
    check record = case envelopeBody (storedDraft record) of
      AssessmentRecord _ ->
        let targets = filter ((== "assessment_of") . referenceRole)
              (envelopeReferences (storedDraft record))
        in if null targets
             then [issue MissingAssessmentTarget record (Just (envelopeObject (storedDraft record)))
                    "assessment has no assessment_of reference"]
             else concatMap (checkTarget record) targets
      _ -> []

    checkTarget record target = case referenceRevision target of
      Nothing -> [issue UnpinnedAssessmentTarget record (Just (referenceObject target))
        "assessment_of must pin an exact claim revision"]
      Just _revision -> case lookupRevision index target of
        Nothing -> [] -- dangling/wrong pin is reported by referenceIssues
        Just targetRecord -> case envelopeBody (storedDraft targetRecord) of
          ClaimRecord _ -> []
          _ -> [issue AssessmentTargetWrongShape record (Just (referenceObject target))
                  "assessment_of must target a claim revision"]

contractIssues :: DoctorConfig -> [StoredEnvelope] -> [DoctorIssue]
contractIssues config records = concatMap checkRecord records
  where
    checkRecord record =
      let draft = storedDraft record
          required = case envelopeBody draft of
            ClaimRecord _ -> True
            AssessmentRecord _ -> True
            _ -> False
          citations = envelopeContracts draft
          missing = [issue MissingContractCitation record (Just (envelopeObject draft))
            "claim and assessment records require at least one contract citation"
            | required && null citations]
      in missing <> concatMap (checkCitation record) citations

    checkCitation record citation =
      let key = ContractKey (contractId citation) (contractVersion citation)
            (contractDigest citation)
          syntax = parseSha256Digest (contractDigest citation)
          base = [issue InvalidContractCitation record (Just (envelopeObject (storedDraft record)))
                    "contract citation has an empty id, version, or invalid digest"
                  | Text.null (contractId citation)
                    || Text.null (contractVersion citation)
                    || isLeft syntax]
          catalog = case installedContracts config of
            Nothing -> []
            Just known
              | Set.member key known -> []
              | otherwise -> [issue ContractNotInstalled record
                  (Just (envelopeObject (storedDraft record)))
                  ("contract citation is not in the installed catalog: " <> contractId citation)]
      in base <> catalog

contractStatusFor :: DoctorConfig -> CheckStatus
contractStatusFor config = case installedContracts config of
  Nothing -> CheckNotRun "no installed contract catalog was supplied"
  Just _
    | contractSchemasValidated config -> CheckPassed
    | otherwise -> CheckIncomplete
        "citation membership was checked; contract schema and reference-role validation were not supplied"

coverage :: [StoredEnvelope] -> DoctorCoverage
coverage records =
  let bodyShapes = map (bodyShape . envelopeBody . storedDraft) records
      references = concatMap referencesOf records
      supersessions = concatMap (envelopeSupersedes . storedDraft) records
      targets = [ reference
        | record <- records
        , AssessmentRecord _ <- [envelopeBody (storedDraft record)]
        , reference <- envelopeReferences (storedDraft record)
        , referenceRole reference == "assessment_of"
        ]
      contracts = concatMap (envelopeContracts . storedDraft) records
      objects = Set.fromList (map (envelopeObject . storedDraft) records)
  in DoctorCoverage
       { coverageRecords = length records
       , coverageObjects = Set.size objects
       , coverageArtifacts = count Artifact bodyShapes
       , coverageEntities = count Entity bodyShapes
       , coverageClaims = count Claim bodyShapes
       , coverageAssessments = count Assessment bodyShapes
       , coverageReferences = length references
       , coveragePinnedReferences = length (filter (hasRevision . referenceRevision) references)
       , coverageUnpinnedReferences = length (filter (not . hasRevision . referenceRevision) references)
       , coverageSupersessions = length supersessions
       , coverageAssessmentTargets = length targets
       , coverageContractCitations = length contracts
       }
  where
    referencesOf record = envelopeReferences (storedDraft record)
      <> envelopeSupersedes (storedDraft record)
    count value = length . filter (== value)

lookupRevision :: IdentityIndex -> Reference -> Maybe StoredEnvelope
lookupRevision index reference = do
  revision <- referenceRevision reference
  candidates <- Map.lookup (referenceObject reference) (byObject index)
  headMay [candidate | candidate <- candidates, storedRevision candidate == revision]

bodyShape :: RecordBody -> Shape
bodyShape body = case body of
  ArtifactRecord _ -> Artifact
  EntityRecord _ -> Entity
  ClaimRecord _ -> Claim
  AssessmentRecord _ -> Assessment

sortIssues :: [DoctorIssue] -> [DoctorIssue]
sortIssues = sortOnKey
  where
    sortOnKey = sortBy issueKey
    issueKey value = (issueCode value, issueRecord value, issueObject value, issueDetail value)
    sortBy comparator values =
      let insert value [] = [value]
          insert value (headValue:tailValues)
            | compare (comparator value) (comparator headValue) <= EQ =
                value : headValue : tailValues
            | otherwise = headValue : insert value tailValues
      in foldl' (flip insert) [] values

findCycles :: [(RevisionId, RevisionId)] -> [[RevisionId]]
findCycles edges = Set.toList (Set.fromList (map canonicalCycle cycles))
  where
    adjacency = Map.fromListWith (<>)
      [ (newer, [older]) | (newer, older) <- edges ]
    cycles = mapMaybe (cycleFrom edges) (Map.keys adjacency)
    cycleFrom _allEdges start = walk [] start
      where
        next node = Map.findWithDefault [] node adjacency
        walk path node
          | node `elem` path = Just (dropWhile (/= node) path <> [node])
          | otherwise = firstJust [walk (path <> [node]) child | child <- next node]
        firstJust [] = Nothing
        firstJust (value:values) = case value of
          Just found -> Just found
          Nothing -> firstJust values
    canonicalCycle cycleNodes =
      let base = case reverse cycleNodes of
            (_:rest) -> reverse rest
            [] -> []
          rotations = [drop offset base <> take offset base
                      | offset <- [0 .. length base - 1]]
          chosen = case rotations of
            [] -> []
            _ -> minimum rotations
      in case chosen of
           [] -> []
           (first:_) -> chosen <> [first]

issue :: IssueCode -> StoredEnvelope -> Maybe ObjectId -> Text -> DoctorIssue
issue code record objectId detail = DoctorIssue
  { issueCode = code
  , issueRecord = Just (storedRevision record)
  , issueObject = objectId
  , issueDetail = detail
  }

issueForRevision :: IssueCode -> Maybe RevisionId -> Maybe ObjectId -> Text -> DoctorIssue
issueForRevision code revision objectId detail = DoctorIssue
  { issueCode = code
  , issueRecord = revision
  , issueObject = objectId
  , issueDetail = detail
  }

headMay :: [a] -> Maybe a
headMay [] = Nothing
headMay (value:_) = Just value

hasRevision :: Maybe RevisionId -> Bool
hasRevision = maybe False (const True)

isLeft :: Either a b -> Bool
isLeft (Left _) = True
isLeft (Right _) = False

objectText :: ObjectId -> Text
objectText = objectIdText

revisionText :: RevisionId -> Text
revisionText = revisionIdText

decimal :: Int -> Text
decimal = Text.pack . show
