{-# LANGUAGE OverloadedStrings #-}

module Main (main) where

import Archive.Doctor.Checks
  ( CheckStatus (..)
  , ContractKey (..)
  , DoctorConfig (..)
  , DoctorIssue (..)
  , DoctorReport (..)
  , DoctorCoverage (..)
  , IssueCode (..)
  , doctorWithConfig
  )
import Archive.Door.Bootstrap (InitResult (..), initArchive)
import Archive.Door.MutationError (mutationErrorMessage)
import Archive.Door.RecordCommit (prunePackedGitObjects, repackGitObjects)
import Archive.Door.Layout (manifestArchiveId, manifestSpec)
import Archive.Door.Types
  ( RecordBody (..)
  , ContractCitation (contractDigest, contractId, contractVersion)
  , StoredEnvelope
  , envelopeBody
  , envelopeContracts
  , envelopeObject
  , envelopeReferences
  , envelopeSupersedes
  , referenceRevision
  , storedDraft
  , storedRevision
  )
import Archive.Read.Core
  ( CommittedArchive (..)
  , CommittedRecord (..)
  , ReadIssue (..)
  , ReadOutcome (..)
  , SinceOutcome (..)
  , readCommittedArchive
  , readCommittedArchiveSince
  , verifyCommittedObjects
  )
import qualified Data.Aeson as Aeson
import qualified Data.Aeson.Key as Key
import Data.Aeson ((.=))
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.ByteString.Lazy.Char8 as LazyChar8
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text
import System.Environment (getArgs)
import System.Exit (ExitCode (ExitFailure), exitWith, die)
import System.IO (hPutStrLn, stderr)
import qualified Archive.Door.Types as Types

main :: IO ()
main = do
  arguments <- getArgs
  case arguments of
    ["init", "--operator", operator, root] -> do
      result <- initArchive (Text.pack operator) root
      case result of
        Left problem -> die ("archive: " <> Text.unpack (mutationErrorMessage problem))
        Right initialized -> LazyChar8.putStrLn (Aeson.encode (Aeson.object
          [ "archive_id" .= initArchiveId initialized
          , "operator_id" .= initOperatorId initialized
          ]))
    ["maintenance", "repack"] -> do
      repacked <- repackGitObjects "."
      case repacked of
        Left problem -> failCommand 1 (Aeson.object
          [ "status" .= ("failed" :: Text)
          , "error" .= mutationErrorMessage problem
          ])
        Right () -> succeed (Aeson.object ["status" .= ("ok" :: Text)])
    ["maintenance", "prune-packed"] -> do
      result <- prunePackedGitObjects "."
      case result of
        Left problem -> failCommand 1 (Aeson.object
          [ "status" .= ("failed" :: Text)
          , "error" .= mutationErrorMessage problem
          ])
        Right () -> succeed (Aeson.object ["status" .= ("ok" :: Text)])
    ["status"] -> runRead statusOutput
    ["doctor"] -> runDoctor
    ["ls"] -> runRead lsOutput
    ["show", selector] -> runRead (showOutput (Text.pack selector))
    ["current"] -> failCommand 2 (Aeson.object
      [ "status" .= ("unsupported" :: Text)
      , "error" .= ("unsupported: no current rule is defined" :: Text)
      ])
    ["export"] -> runExport
    ["export", "--since", since] -> runExportSince (Text.pack since)
    _ -> failCommand 1 (Aeson.object
      [ "status" .= ("failed" :: Text)
      , "error" .= ("usage: archive (init --operator <identity-name> <root> | maintenance (repack | prune-packed) | status | doctor | ls | show <object-or-revision> | current | export [--since <commit>])" :: Text)
      ])

type Fields = [(Key.Key, Aeson.Value)]

runRead :: (CommittedArchive -> Fields) -> IO ()
runRead render = do
  result <- readCommittedArchive "."
  case result of
    ReadFailed issues -> failCommand 1 (Aeson.object
      [ "status" .= ("failed" :: Text)
      , "issues" .= map issueValue issues
      ])
    ReadComplete archive -> succeed (Aeson.object ("status" .= ("ok" :: Text) : render archive))
    ReadIncomplete archive issues -> failCommand 2 (Aeson.object
      ("status" .= ("incomplete" :: Text)
        : "issues" .= map issueValue issues
        : render archive))

-- World consumes this all-or-nothing export. In particular, an incomplete
-- read must never look like a complete graph built from the usable subset.
runExport :: IO ()
runExport = do
  result <- readCommittedArchive "."
  case result of
    ReadFailed issues -> failExport 1 issues
    ReadIncomplete _ issues -> failExport 2 issues
    ReadComplete archive -> succeed (Aeson.object
      [ "status" .= ("ok" :: Text)
      , "snapshot" .= committedSnapshotCommit archive
      , "archive_id" .= (manifestArchiveId <$> committedManifest archive)
      , "spec" .= (manifestSpec <$> committedManifest archive)
      , "context" .= committedContext archive
      , "records" .= map committedValue (committedRecords archive)
        -- Every installed bundle, cited or not, so a reader can confirm a
        -- contract before its first use (ADR 0059).
      , "contracts" .= committedInstalledBundles archive
      ])

-- | Export what was added after an earlier commit (ADR 0055): the records
-- whose files Git lists as added after it, and, separately, every earlier
-- record those records pin through refs or supersedes. World's projection of
-- a record looks only at the record and its direct pins, so the pins are not
-- followed further. The contracts are those cited by the emitted records.
runExportSince :: Text -> IO ()
runExportSince since = do
  result <- readCommittedArchiveSince "." since
  case result of
    SinceRefused problem -> failCommand 1 (Aeson.object
      [ "status" .= ("failed" :: Text)
      , "since" .= since
      , "error" .= problem
      ])
    SinceRead (ReadFailed issues) _ -> failExport 1 issues
    SinceRead (ReadIncomplete _ issues) _ -> failExport 2 issues
    SinceRead (ReadComplete archive) addedPaths -> do
      let records = committedRecords archive
          added = filter ((`Set.member` addedPaths) . committedPath) records
          addedRevisions = Set.fromList (map (storedRevision . committedEnvelope) added)
          pins = Set.fromList
            [ revision
            | record <- added
            , let draft = storedDraft (committedEnvelope record)
            , Just revision <- map referenceRevision
                (envelopeReferences draft <> envelopeSupersedes draft)
            ]
          pinned = filter (\record -> let revision = storedRevision (committedEnvelope record)
                                      in revision `Set.member` pins
                                           && not (revision `Set.member` addedRevisions)) records
          cited = Set.fromList
            [ (contractId citation, contractVersion citation)
            | record <- added <> pinned
            , citation <- envelopeContracts (storedDraft (committedEnvelope record))
            ]
          contracts = filter ((`Set.member` cited) . bundleKey) (committedContractBundles archive)
      succeed (Aeson.object
        [ "status" .= ("ok" :: Text)
        , "snapshot" .= committedSnapshotCommit archive
        , "since" .= since
        , "archive_id" .= (manifestArchiveId <$> committedManifest archive)
        , "spec" .= (manifestSpec <$> committedManifest archive)
        , "context" .= committedContext archive
        , "records" .= map committedValue added
        , "pinned_records" .= map committedValue pinned
        , "contracts" .= contracts
        ])
  where
    -- Core validated each bundle against its citation, so its id and
    -- version are the cited ones.
    bundleKey value = case value of
      Aeson.Object fields -> (textField "id" fields, textField "version" fields)
      _ -> ("", "")
    textField key fields = case KeyMap.lookup key fields of
      Just (Aeson.String value) -> value
      _ -> ""

failExport :: Int -> [ReadIssue] -> IO ()
failExport code issues = do
  hPutStrLn stderr ("archive export: committed read is incomplete: " <> show issues)
  exitWith (ExitFailure code)

runDoctor :: IO ()
runDoctor = do
  result <- readCommittedArchive "."
  objectIssues <- verifyCommittedObjects "."
  case result of
    ReadFailed issues -> failCommand 1 (Aeson.object
      [ "status" .= ("failed" :: Text)
      , "issues" .= map issueValue (issues <> objectIssues)
      ])
    ReadComplete archive -> finish archive objectIssues
    ReadIncomplete archive issues -> finish archive (issues <> objectIssues)
  where
    finish archive readIssues =
      let report = doctorReport archive
          allIssues = map doctorIssueValue (doctorIssues report)
              <> map issueValue readIssues
          fields = doctorFields report archive
      in if null readIssues && null (doctorIssues report)
           then succeed (Aeson.object
             ("status" .= ("ok" :: Text)
               : "issues" .= ([] :: [Aeson.Value])
               : fields))
           else failCommand (if null readIssues then 1 else 2)
             (Aeson.object
               ("status" .= (if null readIssues then ("failed" :: Text) else "incomplete")
                 : "issues" .= allIssues
                 : fields))

succeed :: Aeson.Value -> IO ()
succeed value = LazyChar8.putStrLn (Aeson.encode value)

failCommand :: Int -> Aeson.Value -> IO ()
failCommand code value = do
  LazyChar8.putStrLn (Aeson.encode value)
  exitWith (ExitFailure code)

statusOutput :: CommittedArchive -> Fields
statusOutput archive = archiveFields archive

lsOutput :: CommittedArchive -> Fields
lsOutput archive =
  [ "records" .= map recordSummary (committedRecords archive) ]
  <> archiveFields archive

showOutput :: Text -> CommittedArchive -> Fields
showOutput selector archive =
  [ "selector" .= selector
  , "records" .= map committedValue selected
  ]
  <> archiveFields archive
  where
    selected = filter (matches selector) (committedRecords archive)
    matches value record =
      let draft = storedDraft (committedEnvelope record)
          revision = revisionText (storedRevision (committedEnvelope record))
          objectId = objectText (envelopeObject draft)
      in value == revision || value == objectId

doctorFields :: DoctorReport -> CommittedArchive -> Fields
doctorFields report archive =
  [ "doctor_issue_count" .= length (doctorIssues report)
  , "coverage" .= coverageValue (doctorCoverage report)
  , "contracts" .= checkValue (doctorContractCheck report)
  , "current" .= checkValue (doctorCurrentCheck report)
  ]
  <> archiveFields archive

doctorReport :: CommittedArchive -> DoctorReport
doctorReport archive = doctorWithConfig config envelopes
  where
    records = committedRecords archive
    envelopes = map committedEnvelope records
    catalog = Set.fromList
      [ ContractKey (contractId citation) (contractVersion citation) (contractDigest citation)
      | envelope <- envelopes
      , citation <- envelopeContracts (storedDraft envelope)
      ]
    config = DoctorConfig (Just catalog) (all committedContractsValid records)

archiveFields :: CommittedArchive -> Fields
archiveFields archive =
  [ "archive_id" .= (manifestArchiveId <$> committedManifest archive)
  , "spec" .= (manifestSpec <$> committedManifest archive)
  , "context_present" .= maybe False (const True) (committedContext archive)
  , "record_count" .= length (committedRecords archive)
  ]

recordSummary :: CommittedRecord -> Aeson.Value
recordSummary record =
  let envelope = committedEnvelope record
      draft = storedDraft envelope
  in Aeson.object
       [ "revision_id" .= storedRevisionText envelope
       , "object_id" .= objectText (envelopeObject draft)
       , "shape" .= shapeText (envelopeBody draft)
       , "path" .= committedPath record
       ]

doctorIssueValue :: DoctorIssue -> Aeson.Value
doctorIssueValue issue = Aeson.object
  [ "code" .= issueCodeText (issueCode issue)
  , "revision_id" .= fmap revisionText (issueRecord issue)
  , "object_id" .= fmap objectText (issueObject issue)
  , "detail" .= issueDetail issue
  ]

coverageValue :: DoctorCoverage -> Aeson.Value
coverageValue coverage = Aeson.object
  [ "records" .= coverageRecords coverage
  , "objects" .= coverageObjects coverage
  , "artifacts" .= coverageArtifacts coverage
  , "entities" .= coverageEntities coverage
  , "claims" .= coverageClaims coverage
  , "assessments" .= coverageAssessments coverage
  , "references" .= coverageReferences coverage
  , "pinned_references" .= coveragePinnedReferences coverage
  , "unpinned_references" .= coverageUnpinnedReferences coverage
  , "supersessions" .= coverageSupersessions coverage
  , "assessment_targets" .= coverageAssessmentTargets coverage
  , "contract_citations" .= coverageContractCitations coverage
  ]

checkValue :: CheckStatus -> Aeson.Value
checkValue status = case status of
  CheckPassed -> Aeson.object [ "status" .= ("passed" :: Text) ]
  CheckNotRun detail -> Aeson.object
    [ "status" .= ("not_run" :: Text), "detail" .= detail ]
  CheckIncomplete detail -> Aeson.object
    [ "status" .= ("incomplete" :: Text), "detail" .= detail ]

issueValue :: ReadIssue -> Aeson.Value
issueValue issue = case issue of
  GitFailure detail -> readIssueObject "git" Nothing detail
  MissingTree path -> readIssueObject "missing_tree" (Just path) "committed tree is absent"
  ParseFailure path detail -> readIssueObject "parse" (Just path) detail
  UnsupportedFailure path detail -> readIssueObject "unsupported" (Just path) detail

readIssueObject :: Text -> Maybe FilePath -> Text -> Aeson.Value
readIssueObject kind path detail = Aeson.object
  [ "kind" .= kind
  , "path" .= path
  , "detail" .= detail
  ]

shapeText :: RecordBody -> Text
shapeText body = case body of
  ArtifactRecord _ -> "artifact"
  EntityRecord _ -> "entity"
  ClaimRecord _ -> "claim"
  AssessmentRecord _ -> "assessment"

storedRevisionText :: StoredEnvelope -> Text
storedRevisionText = revisionText . storedRevision

objectText :: Types.ObjectId -> Text
objectText = Types.objectIdText

revisionText :: Types.RevisionId -> Text
revisionText = Types.revisionIdText

issueCodeText :: IssueCode -> Text
issueCodeText code = Text.pack (case code of
  DuplicateRevision -> "duplicate_revision"
  DuplicateObjectRevision -> "duplicate_object_revision"
  DanglingReference -> "dangling_reference"
  WrongRevisionPin -> "wrong_revision_pin"
  UnpinnedSupersession -> "unpinned_supersession"
  InvalidSupersessionRole -> "invalid_supersession_role"
  SupersessionWrongObject -> "supersession_wrong_object"
  SupersessionWrongShape -> "supersession_wrong_shape"
  SupersessionFork -> "supersession_fork"
  SupersessionCycle -> "supersession_cycle"
  MissingAssessmentTarget -> "missing_assessment_target"
  UnpinnedAssessmentTarget -> "unpinned_assessment_target"
  AssessmentTargetWrongShape -> "assessment_target_wrong_shape"
  MissingContractCitation -> "missing_contract_citation"
  InvalidContractCitation -> "invalid_contract_citation"
  ContractNotInstalled -> "contract_not_installed")
