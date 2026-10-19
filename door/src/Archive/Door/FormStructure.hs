{-# LANGUAGE OverloadedStrings #-}

-- | @x-archive-form-structure@ (D-18): the rules of a form's sections,
-- repeat endings, and jumps that JSON Schema cannot state.
--
-- Located sections use one kind of locator and never go back. A repeat's
-- endings follow a non-empty body one after another, the last ending at the
-- repeat's last section, and every pass has exactly one ending; a repeat
-- inside one with endings lies within its body or one ending. A jump's fields
-- are those its kind and ending need, within the sections; where play
-- resumes and the coda are never strictly inside a repeat; and jumps are at
-- distinct sections in written order. That repeats nest or are apart is
-- @x-archive-nested-ranges@'s rule.
module Archive.Door.FormStructure (validateFormStructure) where

import Archive.Door.RuleJson
import Data.Aeson (Value (..))
import Data.Aeson.KeyMap qualified as KeyMap
import Data.List (sort)
import Data.Maybe (isJust)
import Data.Text (Text)

data Repeat = Repeat { repeatFirst :: Integer, repeatLast :: Integer, repeatParts :: [(Integer, Integer)] }

validateFormStructure :: KeyMap.KeyMap Value -> NamedFields -> Either Text ()
validateFormStructure payload named = do
  let sections = [fields | Object fields <- arrayAt (named "sections") payload]
      count = fromIntegral (length sections) :: Integer
  locators sections
  repeats <- mapM repeatOf [fields | Object fields <- arrayAt (named "repeats") payload]
  mapM_ (\(index, outer) -> mapM_ (\inner ->
      if repeatFirst outer <= repeatFirst inner && repeatLast inner <= repeatLast outer
          && not (any (\(low, high) -> low <= repeatFirst inner && repeatLast inner <= high) (repeatParts outer))
        then Left (named "repeats" <> " must not cross the body and an ending of another repeat")
        else Right ()) [inner | (other, inner) <- zip [0 :: Int ..] repeats, other /= index])
    (zip [0 ..] repeats)
  afters <- mapM (jump count repeats) [fields | Object fields <- arrayAt (named "jumps") payload]
  if afters == sort afters && and (zipWith (/=) afters (drop 1 afters)) then Right ()
    else Left (named "jumps" <> " must be at distinct sections, in written order")
  where
    locators sections = go Nothing [locator | fields <- sections, Just locator <- [objectAt "locator" fields]]
      where
        go _ [] = Right ()
        go previous (locator : rest) = do
          here <- case (integerAt "line" locator, integerAt "bar" locator) of
            (Just line, Nothing) -> Right ("line" :: Text, line)
            (Nothing, Just bar) -> Right ("bar", bar)
            _ -> Left (named "sections" <> " locator must give exactly one of line or bar")
          case previous of
            Just (kind, at) | kind /= fst here || snd here < at ->
              Left (named "sections" <> " must use one kind of locator and never go back")
            _ -> go (Just here) rest
    repeatOf fields = do
      first <- need "first" fields
      lastSection <- need "last" fields
      times <- need "times" fields
      case KeyMap.lookup "endings" fields of
        Nothing -> pure (Repeat first lastSection [(first, lastSection)])
        Just _ -> do
          let endings = [ending | Object ending <- arrayAt "endings" fields]
          ranges <- mapM (\ending -> (,,) <$> need "first" ending <*> need "last" ending
            <*> pure [value | Just value <- map integerOf (arrayAt "passes" ending)]) endings
          endingsTile first lastSection times ranges
          let body = case ranges of
                (low, _, _) : _ -> (first, low - 1)
                [] -> (first, lastSection)
          pure (Repeat first lastSection (body : [(low, high) | (low, high, _) <- ranges]))
    endingsTile first lastSection times ranges = do
      let starts = [low | (low, _, _) <- ranges]
          ends = [high | (_, high, _) <- ranges]
          passes = concat [numbers | (_, _, numbers) <- ranges]
      case starts of
        start : _ | start > first -> Right ()
        _ -> Left (named "repeats" <> " endings must follow a non-empty body")
      if and [low <= high && high <= lastSection | (low, high, _) <- ranges]
          && and (zipWith (\high low -> low == high + 1) ends (drop 1 starts))
          && (if null ends then False else last ends == lastSection)
        then Right ()
        else Left (named "repeats" <> " endings must follow one another to the repeat's last section")
      if and [and (zipWith (<) numbers (drop 1 numbers)) && not (null numbers) | (_, _, numbers) <- ranges]
          && sort passes == [1 .. times]
        then Right ()
        else Left (named "repeats" <> " endings must give every pass exactly one ending, in ascending order")
    jump count repeats fields = do
      after <- need "after" fields
      kind <- maybe (Left (named "jumps" <> " kind is required")) Right (textAt "kind" fields)
      ending <- maybe (Left (named "jumps" <> " then is required")) Right (textAt "then" fields)
      let has name = isJust (KeyMap.lookup name fields)
      if has "to" /= (kind == "dal_segno") || has "fine" /= (ending == "fine")
          || has "coda" /= (ending == "coda") || has "coda_from" /= (ending == "coda")
        then Left (named "jumps" <> " must give exactly the fields its kind and ending need")
        else Right ()
      within "after" after 0 (count - 1)
      start <- case integerAt "to" fields of
        Just to -> to <$ within "to" to 0 after
        Nothing -> Right 0
      mapM_ (\fine -> within "fine" fine start (count - 1)) (integerAt "fine" fields)
      landings <- case (integerAt "coda_from" fields, integerAt "coda" fields) of
        (Just from, Just coda) -> do
          within "coda_from" from start (count - 2)
          within "coda" coda (max after from + 1) (count - 1)
          pure [start, coda]
        _ -> pure [start]
      if any (\landing -> any (\r -> repeatFirst r < landing && landing <= repeatLast r) repeats) landings
        then Left (named "jumps" <> " must land at a repeat's first section, never inside it")
        else Right ()
      pure after
    within name value low high
      | low <= value && value <= high = Right ()
      | otherwise = Left (named "jumps" <> " " <> name <> " is outside the sections it may name")
    need name fields = maybe (Left (name <> " must be an integer")) Right (integerAt name fields)
