{-# LANGUAGE OverloadedStrings #-}

-- | Why a proposal directory cannot be read or is not a valid proposal.
--
-- 'Archive.Door.Proposal' builds these values and renders them with
-- 'proposalProblemMessage' at its exported edge, so the rest of Door, the
-- service's refusal codes, and the Python suites keep seeing exactly the
-- messages they compare. Keeping every spelling in one function is what lets
-- the checks themselves stay free of text.
module Archive.Door.ProposalProblem
  ( ProposalProblem (..)
  , proposalProblemMessage
  ) where

import Archive.Door.Json.Access (FieldRefusal, fieldRefusalText)
import Data.Text (Text)
import qualified Data.Text as Text

data ProposalProblem
  = ProposalIoFailure Text
    -- ^ an 'IOException' shown as text
  | DescriptorCloseFailed ProposalProblem
  | AtPath FilePath ProposalProblem
  | NotRegularFile
  | ExceedsSizeLimit
  | InvalidJson Text String
  | InvalidDraft Text Text
    -- ^ the record path and the draft parser's own message
  | RecheckFailed ProposalProblem
  | ManifestChanged
  | RecordBytesChanged
  | TransitionArraysEmpty
  | DuplicateManifestPath
  | RecordCountMismatch
  | HeadsOmitDraft
  | HeadsNotExactlyDrafts
  | IdentitiesOutsideDrafts
  | IdentitiesNotNullHeads
  | ContentsMismatch
  | ContentUndeclared
  | ContentSizeDisagrees
  | ContentSizeNotInteger
  | ContentDigestMissing
  | ContentSizeMissing
  | ContentFieldTypes
  | ContentNotObject
  | ArtifactPayloadNotObject
  | ManifestNotObject
  | WrongProtocol
  | RecordPathShape
  | RecordPathNotString
  | PathEmpty
  | PathAbsolute
  | PathHasNul
  | PathUnsafeComponent
  | PathNotNormalized
  | AllocationNotObject
  | DeclarationNotObject
  | ExpectedRevisionInvalid
  | ExpectedHeadNotObject
  | DeclaredSizeNotInteger
  | DigestInvalid
  | ObjectUrnInvalid
  | NamespaceInvalid
  | IdentityNameInvalid
  | UnknownOrMissingFields Text
    -- ^ the object's label, such as @proposal manifest@
  | ProposalField FieldRefusal
  | ControlCharacters Text
  | NotUnique Text
  deriving (Eq, Show)

proposalProblemMessage :: ProposalProblem -> Text
proposalProblemMessage problem = case problem of
  ProposalIoFailure shown -> "proposal I/O failure: " <> shown
  DescriptorCloseFailed inner -> "proposal descriptor close failed: " <> proposalProblemMessage inner
  AtPath path inner -> Text.pack path <> ": " <> proposalProblemMessage inner
  NotRegularFile -> "must be a regular file"
  ExceedsSizeLimit -> "exceeds the proposal size limit"
  InvalidJson label parser -> "invalid JSON in " <> label <> ": " <> Text.pack parser
  InvalidDraft path parser -> "invalid draft " <> path <> ": " <> parser
  RecheckFailed inner -> "proposal recheck failed: " <> proposalProblemMessage inner
  ManifestChanged -> "proposal manifest changed during operation"
  RecordBytesChanged -> "proposal record bytes changed during operation"
  TransitionArraysEmpty -> "transition manifest arrays must be non-empty"
  DuplicateManifestPath -> "manifest records contains a duplicate path"
  RecordCountMismatch -> "proposal record count does not match manifest"
  HeadsOmitDraft -> "expected_heads must include every drafted object"
  HeadsNotExactlyDrafts -> "expected_heads must contain exactly the drafted objects"
  IdentitiesOutsideDrafts -> "identities must refer to drafted objects"
  IdentitiesNotNullHeads -> "identities must equal the objects with null expected heads"
  ContentsMismatch -> "contents must match artifact content references exactly"
  ContentUndeclared -> "artifact content is not declared in contents"
  ContentSizeDisagrees -> "content size disagrees with artifact payload"
  ContentSizeNotInteger -> "artifact content size is not a non-negative integer"
  ContentDigestMissing -> "artifact content digest is missing"
  ContentSizeMissing -> "artifact content size is missing"
  ContentFieldTypes -> "artifact content digest and size have invalid types"
  ContentNotObject -> "artifact content is not an object"
  ArtifactPayloadNotObject -> "artifact payload is not an object"
  ManifestNotObject -> "proposal manifest must be a JSON object"
  WrongProtocol -> "proposal manifest has the wrong protocol"
  RecordPathShape -> "record path must be records/<name>.json"
  RecordPathNotString -> "manifest records entries must be strings"
  PathEmpty -> "proposal path must not be empty"
  PathAbsolute -> "proposal path must be relative"
  PathHasNul -> "proposal path must not contain NUL"
  PathUnsafeComponent -> "proposal path contains an unsafe component"
  PathNotNormalized -> "proposal path is not normalized"
  AllocationNotObject -> "identity allocation must be an object"
  DeclarationNotObject -> "content declaration must be an object"
  ExpectedRevisionInvalid -> "expected_heads revision_id must be null or a digest"
  ExpectedHeadNotObject -> "expected head must be an object"
  DeclaredSizeNotInteger -> "size must be a non-negative integer"
  DigestInvalid -> "digest must be sha256 followed by 64 lowercase hexadecimal characters"
  ObjectUrnInvalid -> "object_id must be a lowercase urn:uuid identifier"
  NamespaceInvalid -> "identity namespace has an invalid segment"
  IdentityNameInvalid -> "identity name has invalid characters"
  UnknownOrMissingFields label -> label <> " has unknown or missing fields"
  ProposalField refusal -> fieldRefusalText refusal
  ControlCharacters field -> field <> " must not contain control characters"
  NotUnique label -> label <> " must be unique"
