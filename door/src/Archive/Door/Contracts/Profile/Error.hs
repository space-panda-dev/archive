{-# LANGUAGE OverloadedStrings #-}

-- | Why a profile rule is refused while its bundle loads ('RuleError'), and
-- why an instance breaks a rule ('RuleViolation').
--
-- Each value carries the rule's label and the field names it is about, so a
-- caller can tell refusals apart without matching text; the text the suites
-- compare is written once, by 'renderRuleError' and 'renderRuleViolation',
-- where the refusal leaves "Archive.Door.Contracts". A label is the rule's
-- name as its refusals spell it, such as @"pitch stacks rule"@.
module Archive.Door.Contracts.Profile.Error
  ( RuleError (..)
  , TargetProblem (..)
  , renderRuleError
  , RuleViolation (..)
  , renderRuleViolation
  ) where

import Archive.Door.Contracts.Fields (FieldError, FromFieldError (..), renderFieldError)
import Data.Text (Text)
import Data.Text qualified as Text

-- | A malformed profile rule.
data RuleError
  = RuleField FieldError
  | RuleNotObject Text
  -- | A rule (named by the label) whose named fields must all differ.
  | FieldsNotDistinct Text
  -- | A rule (named by the label) that names exactly two different fields.
  | TwoFieldsNotDistinct Text
  | TooFewDistinctRoles
  | DatesOrderNotOwnFields
  | SubsetOfItself
  | WithinNotDistinct
  | RangeBoundsSame
  | SameSubjectOneRole
  | WindowBoundsSame
  | OptionalNotTrue
  -- | A paths rule (named by the label) with no @paths@, or none in it.
  | PathsMissing Text
  | NoPath Text
  -- | A path (named by the label) that is not a nonempty array of names,
  -- holds an empty name, or repeats; and a list of paths that is no array.
  | PathNotFieldNames Text
  | PathEmptyName Text
  | PathRepeated Text
  | PathsNotArray Text
  | DistinctContentWithoutSpans
  | DistinctContentRole
  -- | A rule (named by the label) whose role has no single Claim target.
  | NotSingleClaimTarget Text
  | TargetRule TargetProblem
  | RuleSchemaNotObject
  -- | Text from "Archive.Door.SpanRules", whose rules are shared with
  -- Door's record rules and keep their own wording.
  | ForeignRule Text
  deriving (Eq, Show)

instance FromFieldError RuleError where
  fieldError = RuleField

-- | What is wrong with an @x-archive-reference-targets@ rule.
data TargetProblem
  = RoleEmpty
  | TargetsNotObject
  | AlternativesNest
  | TooFewAlternatives
  | AlternativesNameMany
  | AlternativesSame
  | KindNotString
  | KindWithoutEntity
  | KindAndKinds
  | ContractWithoutClaim
  | ManyWithKind
  | ShapeAndShapes
  | ShapeNotString
  | ShapesNotSet
  | ShapesNotArray
  | NoShape
  | ShapesNotStrings
  | UnknownShape
  | ContractNotObject
  | KindsNotSet
  | KindsNotArray
  | KindsNotStrings
  | ManyNotBoolean
  deriving (Eq, Show)

renderRuleError :: RuleError -> Text
renderRuleError problem = case problem of
  RuleField field -> renderFieldError field
  RuleNotObject label -> label <> " must be an object"
  FieldsNotDistinct label -> label <> " must name distinct fields"
  TwoFieldsNotDistinct label -> label <> " must name two distinct fields"
  TooFewDistinctRoles -> "distinct references rule must name at least two distinct roles"
  DatesOrderNotOwnFields -> "partial dates rule must order two of its fields"
  SubsetOfItself -> "subset rule must name two different arrays"
  WithinNotDistinct -> "document order rule must name distinct within fields"
  RangeBoundsSame -> "nested ranges rule must name two bound fields"
  SameSubjectOneRole -> "same subject rule must compare two different roles"
  WindowBoundsSame -> "span window rule must name two distinct bound fields"
  OptionalNotTrue -> "a rule's optional must be true when present"
  PathsMissing label -> label <> " requires paths"
  NoPath label -> label <> " must name at least one path"
  PathNotFieldNames label -> label <> " must be a nonempty array of field names"
  PathEmptyName label -> label <> " must contain non-empty field names"
  PathRepeated label -> label <> " must not repeat a path"
  PathsNotArray label -> label <> " must be an array of paths"
  DistinctContentWithoutSpans -> "distinct track content rule requires a track sample spans rule"
  DistinctContentRole -> "distinct track content rule must name the span reference role"
  NotSingleClaimTarget label -> label <> " role must have a single Claim reference target rule"
  TargetRule target -> renderTargetProblem target
  RuleSchemaNotObject -> "contract schema must be an object"
  ForeignRule text -> text

renderTargetProblem :: TargetProblem -> Text
renderTargetProblem problem = case problem of
  RoleEmpty -> "contract reference target role must not be empty"
  TargetsNotObject -> "contract reference targets must be an object"
  AlternativesNest -> "contract reference target alternatives must not nest"
  TooFewAlternatives -> "contract reference target any must list at least two rules"
  AlternativesNameMany -> "contract reference target alternatives must not name many"
  AlternativesSame -> "contract reference target alternatives must differ"
  KindNotString -> "contract reference target kind must be a non-empty string"
  KindWithoutEntity -> "contract reference target kind is valid only for entity shape"
  KindAndKinds -> "contract reference target may name kind or kinds, not both"
  ContractWithoutClaim -> "contract reference target contract is valid only for claim shape"
  ManyWithKind -> "repeated Entity kind rules are unsupported"
  ShapeAndShapes -> "contract reference target may name shape or shapes, not both"
  ShapeNotString -> "contract reference target shape must be a string"
  ShapesNotSet -> "contract reference target shapes must be a set of at least two shapes"
  ShapesNotArray -> "contract reference target shapes must be an array"
  NoShape -> "contract reference target rule requires shape or shapes"
  ShapesNotStrings -> "contract reference target shapes must contain strings"
  UnknownShape -> "contract reference target shape must be artifact, assessment, claim, or entity"
  ContractNotObject -> "contract reference target contract must be an object"
  KindsNotSet -> "contract reference target kinds must be a nonempty set"
  KindsNotArray -> "contract reference target kinds must be an array"
  KindsNotStrings -> "contract reference target kinds must contain non-empty strings"
  ManyNotBoolean -> "contract reference target many must be a boolean"

-- | An instance that breaks a rule. Most carry the payload field names the
-- rule was given, since the refusal names them.
data RuleViolation
  = ViolationField FieldError
  | InstanceNotObject
  -- | A value (named) that must be an object, an array, a nonempty array,
  -- or hold only objects or only integers.
  | NotObject Text
  | NotArray Text
  | NotNonEmptyArray Text
  | NotObjects Text
  | NotIntegers Text
  | NotIntegersOrStrings Text
  | NotEmpty Text
  | NotText Text
  -- | A path (its field names) met a non-object, or reached a non-integer.
  | PathNotThroughObjects [Text]
  | PathNotIntegers [Text]
  | RefsNotArray
  | WrongRole Text
  | RolesShareObject [Text]
  | RoleRepeatsObject Text
  | RefsNotSorted
  | OrderedSpanCount
  | TrackSpanCount
  | TrackSpansOverlap
  | TrackWithoutSpans
  | BadSampleSpan
  | AnchorsNotIncreasing
  -- | The later field, then the earlier one it must not precede.
  | EndBeforeStart Text Text
  | NotUtcInstant Text
  | NotPartialDate Text
  | NotAscending Text
  -- | The array, then the array it must be drawn from.
  | NotSubset Text Text
  | DocumentBackwards Text
  -- | The ranges, then the array they index.
  | NotRanges Text Text
  | RangesOutOfOrder Text
  | RangesCross Text
  | UncertainOverlaps Text
  -- | The uncertain groups, then their classes field.
  | UncertainUnordered Text Text
  | VoicingBass Text
  | VoicingTones Text
  -- | The grouping, then the numerator it must add up to.
  | GroupingSum Text Text
  | BeatCount Text
  | BeatsMisplaced Text
  -- | The links, then the bars field the layer lacks.
  | LayerWithoutBars Text Text
  | NoSuchLayer Text
  | NoEarlierBar Text
  | BarsNotAdjacent Text
  | PulseUnmoved Text
  | LinksOutOfOrder Text
  -- | The denominator and dots fields.
  | NotNoteValue Text Text
  -- | A rule read from a schema at admission rather than from a loaded
  -- bundle, found malformed there.
  | BrokenRule RuleError
  -- | Text from "Archive.Door.SpanRules", whose checks are shared with
  -- Door's record rules and keep their own wording.
  | ForeignViolation Text
  deriving (Eq, Show)

instance FromFieldError RuleViolation where
  fieldError = ViolationField

renderRuleViolation :: RuleViolation -> Text
renderRuleViolation problem = case problem of
  ViolationField field -> renderFieldError field
  InstanceNotObject -> "contract profile requires an object instance"
  NotObject name -> name <> " must be an object"
  NotArray name -> name <> " must be an array"
  NotNonEmptyArray name -> name <> " must be a nonempty array"
  NotObjects name -> name <> " must contain objects"
  NotIntegers name -> name <> " must contain integers"
  NotIntegersOrStrings name -> name <> " must contain integers or strings"
  NotEmpty name -> name <> " must not be empty"
  NotText name -> name <> " must be text"
  PathNotThroughObjects path -> dotted path <> " must be reached through objects"
  PathNotIntegers path -> dotted path <> " must hold integers"
  RefsNotArray -> "contract instance refs must be an array"
  WrongRole expected -> "reference role must be " <> expected
  RolesShareObject roles -> "references in roles " <> Text.intercalate ", " roles <> " must name different objects"
  RoleRepeatsObject role -> role <> " references must name different objects"
  RefsNotSorted -> "references must be nonempty, unique, and sorted by object and revision"
  OrderedSpanCount -> "ordered sample spans and piece references must have equal nonzero length"
  TrackSpanCount -> "track spans and span references must be nonempty and equal in number"
  TrackSpansOverlap -> "spans of one track that pin the same Artifact must not overlap or run backwards"
  TrackWithoutSpans -> "track must contain a span array"
  BadSampleSpan -> "sample span requires 0 <= start < end and positive sample rate"
  AnchorsNotIncreasing -> "anchors must strictly increase in every named field"
  EndBeforeStart end start -> end <> " must not be before " <> start
  NotUtcInstant name -> name <> " must be an RFC 3339 instant in UTC with a Z suffix"
  NotPartialDate name -> name <> " must begin with a calendar date"
  NotAscending name -> name <> " must be distinct items in ascending order"
  NotSubset items others -> items <> " must be among " <> others
  DocumentBackwards name -> name <> " located in the document must not go backwards"
  NotRanges name others -> name <> " must be ranges of positions in " <> others
  RangesOutOfOrder name -> name <> " must be distinct and in order of first position, outer before inner"
  RangesCross name -> name <> " must nest or be apart, never cross"
  UncertainOverlaps name -> name <> " must not name the bass, a tone, or a class of another group"
  UncertainUnordered name classes -> name <> " groups must be in ascending order of their " <> classes
  VoicingBass name -> name <> "'s lowest note must be the bass"
  VoicingTones name -> name <> "'s upper notes must sound every tone and only tones or uncertain classes"
  GroupingSum name numerator -> name <> " must add up to the bar's " <> numerator
  BeatCount name -> name <> " must hold one sample per counted beat"
  BeatsMisplaced name -> name <> " must start on the bar's downbeat and rise within the bar"
  LayerWithoutBars name bars -> name <> " must join bars of a layer that holds " <> bars
  NoSuchLayer name -> name <> " must name a layer of the claim"
  NoEarlierBar name -> name <> " must name a bar after the first of its layer"
  BarsNotAdjacent name -> name <> " must join bars that are adjacent"
  PulseUnmoved name -> name <> " must move the pulse to a note value of another length"
  LinksOutOfOrder name -> name <> " must be distinct and in order of layer and bar"
  NotNoteValue denominator dots -> denominator <> " and " <> dots <> " must be a note value"
  BrokenRule rule -> renderRuleError rule
  ForeignViolation text -> text
  where
    dotted = Text.intercalate "."
