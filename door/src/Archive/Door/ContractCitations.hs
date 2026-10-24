{-# LANGUAGE OverloadedStrings #-}

-- | A record's contract citations against the bundles a snapshot holds.
--
-- Committed records and proposed ones are checked the same way: each cited
-- bundle is installed, its schema accepts the record's payload and refs, and
-- no two cited bundles define the same context term.
module Archive.Door.ContractCitations
  ( validateCitations
  , validateCitationContexts
  , contractInstance
  ) where

import Archive.Door.Contracts
  ( ContractBundle
      ( contractBundleContext
      , contractBundleDigest
      , contractBundleId
      , contractBundleVersion
      )
  , validateBundleInstance
  )
import Archive.Door.ContractRuleProblem (ContractRuleProblem (..), outsideRule)
import Archive.Door.Distinct (distinctOr)
import Archive.Door.Snapshot.Data (Installed (..))
import Archive.Door.Types (ContractCitation (..))
import Data.Aeson (Value (Object), object, (.=))
import qualified Data.Aeson.Key as Key
import qualified Data.Aeson.KeyMap as KeyMap
import qualified Data.Map.Strict as Map
import qualified Data.Set as Set

validateCitations
  :: [Installed]
  -> Value
  -> [ContractCitation]
  -> Either ContractRuleProblem ()
validateCitations installed value citations = do
  instanceValue <- contractInstance value
  bundles <- mapM (resolveOne instanceValue) citations
  validateCitationContexts bundles
  where
    catalog = Map.fromList [(installedKey item, installedBundle item) | item <- installed]
    resolveOne instanceValue citation = do
      let key = (contractId citation, contractVersion citation, contractDigest citation)
      bundle <- case Map.lookup key catalog of
        Nothing -> Left (CitedNotInstalled (contractId citation))
        Just value' -> Right value'
      outsideRule (validateBundleInstance bundle instanceValue)
      pure bundle

validateCitationContexts :: [ContractBundle] -> Either ContractRuleProblem ()
validateCitationContexts bundles = do
  distinctOr DuplicateCitation
    [ contractBundleId bundle <> "\0" <> contractBundleVersion bundle <> "\0"
        <> contractBundleDigest bundle
    | bundle <- bundles
    ]
  let owners = Map.fromListWith Set.union
        [ (Key.toText term, Set.singleton (contractBundleId bundle))
        | bundle <- bundles
        , term <- KeyMap.keys (contractBundleContext bundle)
        ]
      collisions = [term | (term, values) <- Map.toList owners, Set.size values > 1]
  case collisions of
    [] -> Right ()
    terms -> Left (ContextTermCollision terms)

contractInstance :: Value -> Either ContractRuleProblem Value
contractInstance (Object fields) = do
  payload <- maybe (Left PayloadMissing) Right (KeyMap.lookup "payload" fields)
  refs <- maybe (Left RefsMissing) Right (KeyMap.lookup "refs" fields)
  pure (object ["payload" .= payload, "refs" .= refs])
contractInstance _ = Left EnvelopeNotObject
