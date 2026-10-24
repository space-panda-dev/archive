{-# LANGUAGE OverloadedStrings #-}

-- | The JSON-LD contexts Door accepts: the specification's core context,
-- whose term catalog each specification version fixes, and a bundle's own
-- context, which may add terms but never redefine a core one or import
-- another context.
module Archive.Door.Contracts.Context
  ( validateCoreContext
  , validateCoreContextForSpecification
  , validateContractContext
  ) where

import Archive.Door.Contracts.Fields
  ( expect
  , objectValue
  , requireExactKeys
  , requiredObject
  , requiredText
  )
import Data.Aeson (Value (..))
import Data.Aeson.Key qualified as Key
import Data.Aeson.KeyMap qualified as KeyMap
import Data.Foldable (toList)
import Data.Text (Text)
import Data.Text qualified as Text
import qualified Data.Set as Set

validateCoreContext :: Value -> Either Text ()
validateCoreContext (Object outer) = do
  requireExactKeys "core context" ["@context"] outer
  inner <- requiredObject "@context" outer
  rejectContextDirectives "core context" inner
  let actualKeys = Set.fromList (map Key.toText (KeyMap.keys inner))
  keys <- case filter ((== actualKeys) . Set.fromList) [creditContextKeys, reviewContextKeys, worldContextKeys, coreContextKeys] of
    catalog : _ -> Right catalog
    [] -> Left "core context has an unknown term catalog"
  requireExactKeys "core context terms" keys inner
  expectJsonLdVersion inner
  expectProtected inner
  mapM_ (checkCoreString inner)
    [ ("arch", "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#")
    , ("as", "http://www.w3.org/ns/activitystreams#")
    , ("dcterms", "http://purl.org/dc/terms/")
    , ("prov", "http://www.w3.org/ns/prov#")
    , ("rdf", "http://www.w3.org/1999/02/22-rdf-syntax-ns#")
    , ("rdfs", "http://www.w3.org/2000/01/rdf-schema#")
    , ("xsd", "http://www.w3.org/2001/XMLSchema#")
    , ("spec", "arch:spec"), ("revision_id", "arch:revision_id")
    , ("object_id", "arch:object_id"), ("by", "as:generator")
    , ("agent", "dcterms:creator"), ("payload", "arch:payload")
    , ("refs", "arch:refs"), ("supersedes", "arch:supersedes")
    , ("source", "arch:source"), ("contracts", "arch:contracts")
    , ("contract", "arch:contract"), ("reference", "arch:reference")
    , ("archive_id", "arch:archive_id"), ("layout", "arch:layout")
    , ("artifact", "arch:Artifact"), ("entity", "arch:Entity")
    , ("claim", "arch:Claim"), ("assessment", "arch:Assessment")
    , ("label", "rdfs:label"), ("person", "prov:Person")
    , ("organisation", "prov:Organization"), ("software", "prov:SoftwareAgent")
    , ("model", "arch:ModelAgent"), ("process", "arch:ProcessAgent")
    , ("id", "arch:id"), ("namespace", "arch:namespace")
    , ("name", "arch:name"), ("revision", "arch:revision")
    , ("content", "arch:content"), ("digest", "arch:digest")
    , ("size", "arch:size"), ("schema", "arch:schema")
    , ("version", "arch:version"), ("protocol", "arch:protocol")
    , ("records", "arch:records"), ("identities", "arch:identities")
    , ("contents", "arch:contents"), ("expected_heads", "arch:expected_heads")
    , ("expected", "arch:expected"), ("annex_key", "arch:annex_key")
    , ("staging_remote", "arch:staging_remote")
    ]
  case KeyMap.lookup (Key.fromText "skos") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("skos", "http://www.w3.org/2004/02/skos/core#")
  case KeyMap.lookup (Key.fromText "musical_work") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("musical_work", "arch:MusicalWork")
  case KeyMap.lookup (Key.fromText "recording_session") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("recording_session", "arch:RecordingSession")
  case KeyMap.lookup (Key.fromText "release") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("release", "arch:Release")
  case KeyMap.lookup (Key.fromText "recording") inner of
    Nothing -> Right ()
    Just _ -> checkCoreString inner ("recording", "arch:Recording")
  requiredText "arch" inner >>= expect "Archive namespace" "urn:uuid:3e8b29f8-70c9-403e-bb4c-2280d59eca7f#"
  requiredText "by" inner >>= expect "by mapping" "as:generator"
  requiredText "agent" inner >>= expect "agent mapping" "dcterms:creator"
  validateCoreObjectMapping inner "at" "prov:generatedAtTime" "xsd:dateTime"
  validateCoreObjectMapping inner "shape" "arch:shape" "@vocab"
  validateCoreObjectMapping inner "kind" "rdf:type" "@vocab"
  validateCoreObjectMapping inner "role" "arch:role" "@vocab"
  mapM_ (requiredKey inner)
    [ "spec", "revision_id", "object_id", "shape", "payload", "refs"
    , "supersedes", "source", "contracts", "artifact", "entity", "claim"
    , "assessment", "label", "id", "content", "digest", "size"
    ]
  pure ()
validateCoreContext _ = Left "core context must be a JSON object"

validateCoreContextForSpecification :: Text -> Value -> Either Text ()
validateCoreContextForSpecification specification value = do
  validateCoreContext value
  context <- objectValue "core context" value >>= requiredObject "@context"
  let keys = Set.fromList (map Key.toText (KeyMap.keys context))
      expected = case specification of
        "archive/0.0.1" -> Set.fromList coreContextKeys
        "archive/0.0.2" -> Set.fromList coreContextKeys
        "archive/0.0.3" -> Set.fromList worldContextKeys
        "archive/0.0.4" -> Set.fromList worldContextKeys
        "archive/0.0.5" -> Set.fromList reviewContextKeys
        "archive/0.0.6" -> Set.fromList reviewContextKeys
        "archive/0.0.7" -> Set.fromList reviewContextKeys
        "archive/0.0.8" -> Set.fromList reviewContextKeys
        "archive/0.0.9" -> Set.fromList creditContextKeys
        "archive/0.0.10" -> Set.fromList creditContextKeys
        "archive/0.0.11" -> Set.fromList creditContextKeys
        _ -> Set.empty
  if not (Set.null expected) && keys == expected
    then Right ()
    else Left "core context terms do not match the selected specification version"

coreContextKeys :: [Text]
coreContextKeys =
  [ "@version", "@protected", "arch", "as", "dcterms", "prov", "rdf", "rdfs", "xsd"
  , "spec", "revision_id", "object_id", "shape", "by", "agent", "at", "payload"
  , "refs", "supersedes", "source", "contracts", "contract", "reference", "archive_id"
  , "layout", "artifact", "entity", "claim", "assessment", "kind", "label", "person"
  , "organisation", "software", "model", "process", "role", "id", "namespace", "name"
  , "revision", "content", "digest", "size", "schema", "version", "protocol", "records"
  , "identities", "contents", "expected_heads", "expected", "annex_key", "staging_remote"
  ]

worldContextKeys :: [Text]
worldContextKeys = coreContextKeys <> ["skos", "musical_work"]

reviewContextKeys :: [Text]
reviewContextKeys = worldContextKeys <> ["recording_session"]

-- | Specification 0.0.9 adds the Entity kinds @release@ and @recording@
-- (ADR 0078).
creditContextKeys :: [Text]
creditContextKeys = reviewContextKeys <> ["release", "recording"]

expectJsonLdVersion :: KeyMap.KeyMap Value -> Either Text ()
expectJsonLdVersion fields = case KeyMap.lookup "@version" fields of
  Just (Number value) | value == 1.1 -> Right ()
  _ -> Left "core context @version must be 1.1"

expectProtected :: KeyMap.KeyMap Value -> Either Text ()
expectProtected fields = case KeyMap.lookup "@protected" fields of
  Just (Bool True) -> Right ()
  _ -> Left "core context @protected must be true"

checkCoreString :: KeyMap.KeyMap Value -> (Text, Text) -> Either Text ()
checkCoreString fields (name, expected) =
  requiredText name fields >>= expect (name <> " mapping") expected

validateCoreObjectMapping :: KeyMap.KeyMap Value -> Text -> Text -> Text -> Either Text ()
validateCoreObjectMapping fields name expectedId expectedType = do
  mapping <- requiredObject name fields
  requireExactKeys (name <> " mapping") ["@id", "@type"] mapping
  requiredText "@id" mapping >>= expect (name <> " @id") expectedId
  requiredText "@type" mapping >>= expect (name <> " @type") expectedType

validateContractContext :: KeyMap.KeyMap Value -> Either Text ()
validateContractContext context = do
  rejectContextDirectives "contract context" context
  mapM_ validateTermDefinition (KeyMap.toList context)
  pure ()

validateTermDefinition :: (Key.Key, Value) -> Either Text ()
validateTermDefinition (key, value)
  | Key.toText key `elem` coreContextNames =
      Left ("contract context redefines core term or prefix: " <> Key.toText key)
  | Text.null (Key.toText key) || "@" `Text.isPrefixOf` Key.toText key =
      Left "contract context term name must be non-empty and not a directive"
  | otherwise = case value of
      String term
        | Text.null term -> Left "contract context term definition must not be empty"
        | otherwise -> pure ()
      Object definition -> validateTermDefinitionObject definition
      _ -> Left "contract context term definition must be a string or object"

validateTermDefinitionObject :: KeyMap.KeyMap Value -> Either Text ()
validateTermDefinitionObject definition = do
  let allowed = ["@id", "@type"]
      unknown = filter (`notElem` allowed) (map (Key.toText . fst) (KeyMap.toList definition))
  case unknown of
    [] -> pure ()
    names -> Left ("contract context definition has unknown keys: " <> Text.intercalate ", " names)
  identifier <- requiredText "@id" definition
  if Text.null identifier
    then Left "contract context definition @id must not be empty"
    else pure ()
  case KeyMap.lookup "@type" definition of
    Nothing -> pure ()
    Just (String value)
      | Text.null value -> Left "contract context definition @type must not be empty"
      | otherwise -> pure ()
    Just _ -> Left "contract context definition @type must be a string"

coreContextNames :: [Text]
coreContextNames =
  [ "arch", "as", "dcterms", "prov", "rdf", "rdfs", "xsd"
  , "spec", "revision_id", "object_id", "shape", "by", "agent", "at"
  , "payload", "refs", "supersedes", "source", "contracts", "contract"
  , "reference", "archive_id", "layout", "artifact", "entity", "claim"
  , "assessment", "kind", "label", "person", "organisation", "software"
  , "model", "process", "role", "id", "namespace", "name", "revision"
  , "content", "digest", "size", "schema", "version", "protocol", "records"
  , "identities", "contents", "expected_heads", "expected", "annex_key"
  , "staging_remote", "skos", "musical_work", "recording_session", "release", "recording"
  ]

rejectContextDirectives :: Text -> KeyMap.KeyMap Value -> Either Text ()
rejectContextDirectives label fields = do
  let forbidden = filter (`KeyMap.member` fields) ["@context", "@import"]
  case forbidden of
    [] -> pure ()
    names -> Left (label <> " contains forbidden directive(s): "
      <> Text.intercalate ", " (map Key.toText names))
  mapM_ (rejectNestedDirectives label . snd) (KeyMap.toList fields)

rejectNestedDirectives :: Text -> Value -> Either Text ()
rejectNestedDirectives label (Object fields) = rejectContextDirectives label fields
rejectNestedDirectives label (Array values) = mapM_ (rejectNestedDirectives label) (toList values)
rejectNestedDirectives _ _ = pure ()

requiredKey :: KeyMap.KeyMap Value -> Text -> Either Text ()
requiredKey fields name
  | KeyMap.member (Key.fromText name) fields = Right ()
  | otherwise = Left ("core context is missing " <> name)
