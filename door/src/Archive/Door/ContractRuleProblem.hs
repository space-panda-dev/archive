{-# LANGUAGE OverloadedStrings #-}

-- | Why a record breaks what its cited contracts require of it.
--
-- Citation checks ("Archive.Door.ContractCitations"), reference target
-- rules ("Archive.Door.ReferenceTargets") and the measured payload rules
-- ("Archive.Door.TargetMeasures") return these values for committed and
-- proposed records alike. A proposal's refusal and a snapshot's integrity
-- failure carry the text 'contractRuleMessage' writes; the Python suites and
-- door-test compare it, so this function is the one place it is spelled.
module Archive.Door.ContractRuleProblem
  ( ContractRuleProblem (..)
  , contractRuleMessage
  , outsideRule
  , shapeArticle
  ) where

import Data.Bifunctor (first)
import Data.Text (Text)
import qualified Data.Text as Text

-- | Most constructors carry the names the contract's rule gave the role,
-- field or array involved, since the message repeats them.
data ContractRuleProblem
  -- Citations.
  = CitedNotInstalled Text
    -- ^ the contract identifier
  | ProposedNotInstalled Text
    -- ^ the contract identifier
  | DuplicateCitation
  | ContextTermCollision [Text]
  | ContextNotHeld Text
    -- ^ the record's specification
  | PayloadMissing
  | RefsMissing
  | EnvelopeNotObject
  -- Reference targets.
  | NotExactlyOneReference Text
    -- ^ the role
  | ReferenceUnpinned Text
    -- ^ the role
  | ReferenceNotAdmitted Text Text
    -- ^ the pinned revision and its object
  | TargetWrongShape Text Text
    -- ^ the role and the one shape it must target
  | TargetNotOfShapes Text [Text]
    -- ^ the role and the shapes it may target
  | TargetNoneOf Text [Text]
    -- ^ the role and each alternative, described
  | TargetNotCiting Text Text Text
    -- ^ the role, and the contract identifier and version
  | TargetEntityNoKind Text
  | TargetEntityNotObject Text
  | TargetEntityKind Text [Text]
    -- ^ the role and the kinds it may target
  | UnsupportedTargetShape
  -- Payload rules measured against a pinned Claim.
  | RuleNeedsClaim Text
    -- ^ the rule, such as @target measure@
  | NotAnInteger Text
  | NotAnArray Text
  | NotAnObject Text
  | MustContainObjects Text
  | SpanNotObject
  | MustEqualTarget Text Text
    -- ^ the field and the role
  | MustNotExceed Text Text Text
    -- ^ this record's field, the role, and the pinned Claim's field
  | NeedsOneReference Text Text
    -- ^ what needs it and the role
  | NotSameSubject Text Text
    -- ^ the role and the compared reference role
  | SamplesNotBefore Text Text
    -- ^ the role and the pinned Claim's count field
  | TargetArrayNotArray Text Text
    -- ^ the role and the pinned Claim's array field
  | NotAPosition Text Text Text
    -- ^ the payload path, the role, and the pinned Claim's array field
  | WindowStartNotSpanStart Text Text Text Text
    -- ^ the window field, its start field, the role, and the spans field
  | WindowEndNotSpanEnd Text Text
    -- ^ the window field and its end field
  | TrackSpanCountMismatch
  | RepeatedTrackContent [Text]
  | ArtifactNoContent Text
  | ArtifactNoDigest Text
  -- | A check owned by another module (Contracts' rule readers, the
  -- canonicalizer, release admission) failed; its message passes through.
  | OutsideRule Text
  deriving (Eq, Show)

-- | Lift another module's 'Text' failure into this family unchanged.
outsideRule :: Either Text a -> Either ContractRuleProblem a
outsideRule = first OutsideRule

-- | How a message names a revision of one shape.
shapeArticle :: Text -> Text
shapeArticle shape = case shape of
  "claim" -> "a Claim revision"
  "assessment" -> "an Assessment revision"
  "artifact" -> "an Artifact revision"
  "entity" -> "an Entity revision"
  other -> "a " <> other <> " revision"

contractRuleMessage :: ContractRuleProblem -> Text
contractRuleMessage problem = case problem of
  CitedNotInstalled identifier -> "contract is not installed at this snapshot: " <> identifier
  ProposedNotInstalled identifier -> "proposed contract is not installed at this snapshot: " <> identifier
  DuplicateCitation -> "contract citation must be unique"
  ContextTermCollision terms -> "contract context term collision: " <> Text.intercalate ", " terms
  ContextNotHeld specification ->
    "record spec " <> specification <> " selects a core context this archive does not hold"
  PayloadMissing -> "contract payload is missing"
  RefsMissing -> "contract refs are missing"
  EnvelopeNotObject -> "record envelope must be an object"
  NotExactlyOneReference role -> "contract requires exactly one " <> role <> " reference"
  ReferenceUnpinned role -> "contract reference " <> role <> " must pin an exact revision"
  ReferenceNotAdmitted revision object ->
    "reference revision " <> revision <> " is not an admitted revision of " <> object
  TargetWrongShape role shape -> role <> " reference must target " <> shapeArticle shape
  TargetNotOfShapes role shapes ->
    role <> " reference must target a revision of shape " <> Text.intercalate " or " shapes
  TargetNoneOf role alternatives -> role <> " reference must target " <> Text.intercalate ", or " alternatives
  TargetNotCiting role identifier version ->
    role <> " reference must target a Claim citing " <> identifier <> " version " <> version
  TargetEntityNoKind role -> role <> " target Entity has no string kind"
  TargetEntityNotObject role -> role <> " target Entity payload is not an object"
  TargetEntityKind role kinds ->
    role <> " reference must target an Entity revision of kind " <> Text.intercalate " or " kinds
  UnsupportedTargetShape -> "installed contract has an unsupported reference target shape"
  RuleNeedsClaim rule -> rule <> " rule applies only to a Claim payload"
  NotAnInteger name -> name <> " must be an integer"
  NotAnArray name -> name <> " must be an array"
  NotAnObject name -> name <> " must be an object"
  MustContainObjects name -> name <> " must contain objects"
  SpanNotObject -> "span must be an object"
  MustEqualTarget field role -> field <> " must equal the " <> role <> " claim's " <> field
  MustNotExceed field role bound -> field <> " must not exceed the " <> role <> " claim's " <> bound
  NeedsOneReference subject role -> subject <> " needs exactly one " <> role <> " reference"
  NotSameSubject role equals -> role <> " claim must be about the same " <> equals
    <> " revision as this record's " <> equals <> " reference"
  SamplesNotBefore role count -> "samples must be before the " <> role <> " claim's " <> count
  TargetArrayNotArray role array -> role <> " claim's " <> array <> " must be an array"
  NotAPosition path role array -> path <> " must be a position in the " <> role <> " claim's " <> array
  WindowStartNotSpanStart window start role spans -> window <> " " <> start
    <> " must be the " <> start <> " of one of the " <> role <> " claim's " <> spans
  WindowEndNotSpanEnd window end -> window <> " " <> end
    <> " must be the " <> end <> " of its first span or of a later one"
  TrackSpanCountMismatch -> "track spans and span references must be equal in number"
  RepeatedTrackContent digests -> "tracks must not pin the same content digest: " <> Text.intercalate ", " digests
  ArtifactNoContent role -> role <> " Artifact has no content"
  ArtifactNoDigest role -> role <> " Artifact has no content digest"
  OutsideRule message -> message
