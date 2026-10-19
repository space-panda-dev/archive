{-# LANGUAGE OverloadedStrings #-}

-- | Admission's entry to the 0.0.10 profile rules that read other records
-- (D-18). Admission calls it once per proposed record, after the reference
-- targets, with the records a reference may name: the committed ones and the
-- earlier records of the batch.
module Archive.Door.ReleaseAdmission
  ( effectiveBundles
  , validateReleaseAdmission
  ) where

import Archive.Door.AgentRules (validateMemberSupersession)
import Archive.Door.Contracts (ContractBundle (..))
import Archive.Door.PinnedRules (validatePinnedRules)
import Archive.Door.RuleRecords (ruleRecords)
import Archive.Door.Types (DraftEnvelope)
import Archive.Door.WhenPinned (effectiveSchema)
import Data.Aeson (Value (..))
import Data.Text (Text)

-- | Each cited bundle with the schema this record is checked under.
effectiveBundles :: Value -> [ContractBundle] -> Either Text [ContractBundle]
effectiveBundles instanceValue = mapM (\bundle -> do
  schema <- effectiveSchema (contractBundleSchema bundle) instanceValue
  pure bundle { contractBundleSchema = schema })

-- | Records are (object, revision, draft).
validateReleaseAdmission :: [(Text, Text, DraftEnvelope)] -> DraftEnvelope -> [ContractBundle] -> Either Text ()
validateReleaseAdmission records draft bundles = do
  let world = ruleRecords records
  mapM_ (\bundle -> case contractBundleSchema bundle of
    Object root -> validatePinnedRules world draft root
    _ -> Right ()) bundles
  validateMemberSupersession world draft
