# 0043. Geographic observations and route handoff

Status: accepted

The walk-finding interface needs to relate outside place listings, the
operator's own observations, generated routes, and navigation. Treating these
as one kind of location data would make it unclear what Archive preserves and
what leaves the operator's control. This decision records the operator's stated
direction while leaving the geographic contracts and World projection outside
the accepted scope of World v1 (ADR 0041).

## Decision

**Outside place listings are candidates.** A listing from OpenStreetMap,
Google, Yelp, or another source describes an outside place for discovery. It is
not by itself an Archive assertion about the operator's experience. World may
show such descriptions as outside-source data, with their source and version
kept distinct from Archive evidence. A listing can help identify a place, but
the stable local identity for that place and the mapping between it and one or
more listings remain to be designed.

**A deliberate save makes a personal assertion.** When the operator chooses to
save a star, rating, or written judgment in the interface, that act proposes a
claim to Archive about the place or walk the operator meant to describe. Its
asserting agent is the operator; a place listing or route engine may inform the
claim but does not become its author. Merely viewing a listing, generating a
route, or passing a location does not create an observation. Archive stores a
saved assertion only through Door, with the claim contract and provenance
required by INV-7, INV-8, and INV-10. The observation's exact shape and the
identity of its target are not decided here.

**A route request is not a visit record.** The operator may give a route
request a temporary preference such as “avoid Myrtle today.” It applies to
that request and is discarded with it unless the operator deliberately saves
it as a lasting preference. A generated route, its ranking, and any chosen
route remain regenerable or operational unless separately admitted as
evidence. No continuous location collection or automatic visit inference is
implied. Whether and how an actually walked GPS track can be captured and
preserved remains open.

**Navigation is a per-route operator action.** After reviewing a route, the
operator may explicitly hand that route from the PWA to Apple Maps. Each
handoff names one chosen route and sends only the route-specific information
needed for that handoff; it does not send the accumulated places, observations,
or tracks as a batch. A home location may be included when it is the chosen
route's origin or destination and the handoff requires it. Naming a chosen
route does not promise transmission of its exact geometry: Apple Maps may
receive endpoints or waypoints and calculate a different path. No application
interface or route presentation is decided here. The handoff is
a user-operated interactive tool action, as distinguished in ADR 0016, not an
Archive-provided read adapter. If Archive later provides code that reads
archival evidence and sends it to an outside routing service, that channel must
follow Archive's disclosure rules and be designed separately.

## Questions left open

- What identifies a local place across changes in names, coordinates, and
  outside listings? Can a World candidate exist before a local place entity is
  asserted?
- What is one saved place or walk observation: one assertion per save, per
  visit, per rating change, or some other unit? How should ratings, prose, date,
  and context be represented?
- Does a walk observation target a generated route, a street or segment, an
  actually walked path, or more than one of these? What evidence is required
  before an observed track is admitted?
- Where does route computation happen, and what origin, destination, route
  geometry, or saved evidence may each routing provider receive?
- What outside response evidence and source-version details must be admitted
  before a listing or route candidate can support a durable claim?
- How may saved preferences influence later route rankings, and what rule
  governs whether an older preference still applies?
- What exactly does the Apple Maps handoff transmit, and can the chosen path be
  preserved or only its endpoints and waypoints?

These questions require geographic contracts and, where they change World or
Archive behavior, a later accepted decision. They do not extend the World v1
MusicBrainz projection or change its current contracts.
