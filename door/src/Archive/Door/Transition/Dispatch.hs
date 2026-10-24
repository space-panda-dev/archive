{-# LANGUAGE OverloadedStrings #-}

-- | Which transition a record is, by its exact contract citation.
--
-- Door commits only the transitions it has a handler for; everything else
-- is refused as unsupported, so a contract cannot be admitted as a
-- transition just because it is installed.
module Archive.Door.Transition.Dispatch
  ( TransitionKind (..)
  , TransitionKey (..)
  , transitionDispatchTable
  , transitionContractsWithoutHandler
  , transitionCitationKind
  , dispatch
  ) where

import Archive.Door.SpecificationUpgrade (upgradeContractId, upgradeContractVersion)
import Archive.Door.Types
  ( ContractCitation (..)
  , DraftEnvelope (..)
  )
import Data.Set (Set)
import qualified Data.Set as Set
import Data.Text (Text)
import qualified Data.Text as Text

data TransitionKind
  = OperatorInitialization
  | Retire
  | Destroy
  | PublicationApproval
  | PublicationWithdrawal
  | SpecificationUpgrade
  deriving (Eq, Show)

-- | The only transition contracts Door can commit, by exact identifier and
-- version (ADRs 0028, 0051, 0068).  Dispatch additionally requires the citation's
-- digest to be installed.  Tests enumerate this table and
-- 'transitionContractsWithoutHandler' against the installed catalog, so a
-- transition contract cannot ship without a handler or a recorded refusal.
transitionDispatchTable :: [((Text, Text), TransitionKind)]
transitionDispatchTable =
  [ (("archive.transition/operator_initialization", "1"), OperatorInitialization)
  , (("archive.transition/retire", "1"), Retire)
  , (("archive.transition/retire", "2"), Retire)
  , (("archive.transition/destroy", "1"), Destroy)
  , (("archive.transition/destroy", "2"), Destroy)
  , (("archive.transition/publication_approval", "1"), PublicationApproval)
  , (("archive.transition/publication_withdrawal", "1"), PublicationWithdrawal)
  , (("archive.transition/publication_approval", "2"), PublicationApproval)
  , (("archive.transition/publication_withdrawal", "2"), PublicationWithdrawal)
  , ((upgradeContractId, upgradeContractVersion), SpecificationUpgrade)
  ]

-- | Installed transition contracts this Door refuses as unsupported. None
-- today: the specification upgrade, the last one 0.0.7 installed without a
-- handler, is dispatched (ADR 0068). A later release that installs a
-- transition before its handler exists lists it here, so it fails loudly.
transitionContractsWithoutHandler :: [(Text, Text)]
transitionContractsWithoutHandler = []

-- | The table entry for a draft's single installed transition citation.
transitionCitationKind :: Set (Text, Text, Text) -> DraftEnvelope -> Maybe (TransitionKind, Text)
transitionCitationKind installed draft = case transitionCitations of
  [key@(identifier, version, _digest)]
    | Set.member key installed ->
        fmap (\kind -> (kind, version)) (lookup (identifier, version) transitionDispatchTable)
  _ -> Nothing
  where
    transitionCitations =
      [ (contractId citation, contractVersion citation, contractDigest citation)
      | citation <- envelopeContracts draft
      , Text.isPrefixOf "archive.transition/" (contractId citation)
      ]

data TransitionKey = TransitionKey
  { transitionKind :: TransitionKind
  , transitionVersion :: Text
  }
  deriving (Eq, Show)

dispatch :: Set (Text, Text, Text) -> DraftEnvelope -> Either Text TransitionKey
dispatch installed draft = case transitionCitationKind installed draft of
  Just (kind, version) -> Right (TransitionKey kind version)
  Nothing -> case [ (identifier, version)
                  | citation <- envelopeContracts draft
                  , let identifier = contractId citation
                        version = contractVersion citation
                  , (identifier, version) `elem` transitionContractsWithoutHandler ] of
    (identifier, version) : _ -> Left ("unsupported transition contract: " <> identifier
      <> " version " <> version <> " is installed but this Door has no handler for it")
    [] -> Left "unsupported transition contract"
