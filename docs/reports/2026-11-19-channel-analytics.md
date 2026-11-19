# Conspiracy Corner: what the channel history says

Date: 2026-11-19. Source: YouTube Data API and YouTube Analytics API, pulled by
`scripts/channel_analytics.py` on lola. It covers all 470 uploads (462 public) from 2025-01-23 to 2026-11-18;
456 have Analytics metrics. The raw pull is in `channel_analytics.json` on lola, which git ignores.

The channel has 388k views and has gained 675 subscribers. All figures below are medians per video, so the
one viral video can't skew them. Titles were grouped by keyword, which is rough but good enough to see the
patterns.

## Summary

1. **Specific, named, place-based stories win.** The channel does best with a story about one real ship,
   doll, set of lights or haunted building, with a place in the title. Broad "big" conspiracy theories got
   almost no views.
2. **Under a minute wins.** Shorts under 60 s get about 2.5 times the views of longer ones, and viewers
   watch more of them.
3. **The quote and meditation videos are the weakest format the channel has made.** At about 100 s long,
   viewers watch around 13 s (13-15%) before leaving. That's after the first quote or two.
4. **About a third of all views come from YouTube search.** Old videos about a named legend keep being
   found. Titles that name the place and the legend are worth more than clever titles.
5. **Too few pipeline videos have data to compare style packs yet.** Only 4 have Analytics metrics, so pack
   weighting has to wait for more data.

## Where views come from

| Source | Views | Share |
|---|---|---|
| Shorts feed | 230,739 | 59% |
| YouTube search | 126,990 | 33% |
| Suggested videos | 17,010 | 4% |
| Subscribers' feeds | 6,237 | 2% |
| Everything else | ~7,500 | 2% |

## By topic

| Topic | Videos | Median views | Median % watched |
|---|---|---|---|
| Cursed object (Robert the Doll, Devil Tree) | 7 | 1,458 | 63% |
| Mystery lights (Chir-Batti, Brown Mountain) | 6 | 1,374 | 73% |
| Ships and the sea (Baychimo, Flying Dutchman) | 19 | 1,071 | 59% |
| Cryptids (Ogopogo, Tahoe Tessie, Melon Heads) | 10 | 868 | 57% |
| Haunted place or local legend | 202 | 602 | 57% |
| Other (film-set curses, exorcisms, debunks of hoaxers) | 120 | 213 | 57% |
| Aircraft and the sky | 14 | 161 | 58% |
| Meditation videos | 15 | 85 | 15% |
| Horror quote videos | 8 | 70 | 14% |
| **Broad conspiracy theories** (Illuminati, moon landing, chemtrails, hollow earth, Bermuda Triangle) | 61 | **6** | 46% |

The broad conspiracy videos were all posted in February and March 2025, when the channel was new. That
partly explains the low numbers. Even so, the Pentagon debunk (12.6k) and Pizzagate (1.3k) did well later,
so the difference is specific versus generic rather than conspiracy versus legend. "The Illuminati: Secret
Society or Paranoid Delusion?" competes with thousands of near-identical videos; "Chir-Batti: The Mysterious
Desert Lights of Gujarat" has almost no competition in search.

### Top 10

| Views | % watched | Length | Title |
|---|---|---|---|
| 61,772 | 95% | 51 s | The Ship That Refused to Sink: SS Baychimo |
| 34,708 | 75% | 22 s | Chir-Batti: The Mysterious Desert Lights of Gujarat |
| 26,358 | 76% | 21 s | Meet Robert The Doll: Key West's Haunted Legend |
| 12,568 | 71% | 65 s | Pentagon Attack: Plane or Missile, Debunk |
| 11,083 | 68% | 52 s | Ogopogo: Canada's Mysterious Lake Monster |
| 8,056 | 56% | 58 s | The Ghosts of Hashima: Japan's Forbidden Haunted Island |
| 4,868 | 49% | 55 s | Gore Orphanage: Ohio's Spooky Legend |
| 3,651 | 73% | 53 s | The Most Haunted Island in England: The Isle of Wight |
| 3,444 | 109% | 17 s | The Haunted Trans-Allegheny "Lunatic" Asylum (WV) |
| 3,312 | 57% | 48 s | The Creepy Truth of Japan's Rabbit Island |

Baychimo and Robert the Doll also brought in the most subscribers (109 and 65).

## By length

These are story videos from April 2025 on. Quote and meditation videos are left out.

| Length | Videos | Median views | Median % watched |
|---|---|---|---|
| Under 30 s | 45 | 619 | 70% |
| 30-44 s | 63 | 672 | 63% |
| 45-59 s | 152 | 688 | 55% |
| 60 s and over | 107 | 255 | 50% |

Anything under a minute performs about the same. Past a minute, views drop sharply.

## Title style

| Title feature | Median views with | Median views without |
|---|---|---|
| Names a place (Ohio, Japan, Key West...) | 954 | 484 |
| Has a colon ("Name: what it is") | 603 | 528 |
| Is a question ("...or Hoax?") | 329 | 582 |
| Has hashtags in the title | 488 | 582 |

## Posting day and time

Saturday uploads have the highest median (739 views), and Tuesday the lowest (282). Almost every upload went
out in the US evening, so time of day can't be compared. These differences are small next to topic and
length, and the effect of the day may come from which stories happened to be posted on it.

## Caveats

- Medians mix eras. The channel was new in early 2025 and dormant for most of 2026, and the Shorts feed may
  have treated each period differently.
- Topics were sorted by title keywords, so a few videos are in the wrong group.
- Public view counts are lifetime totals, so older videos have had longer to collect views.

## Recommendations

1. **Make story Shorts the main daily product.** Each one is one named, real legend or mystery, 30-55 s
   long, with the place in the title: "Name: what it is, where". Pick topics first from the categories
   above: cursed objects, mystery lights, ships, cryptids and haunted places.
2. **For conspiracy topics, go specific.** One incident, one place and one date, told as "what people
   claim, what the record shows". Avoid broad theories that already have thousands of videos.
3. **Rework the meditation and quote format rather than dropping it.** Viewers leave at about 13 s, so:
   - Make it a Short under 60 s with 4-5 quotes.
   - Put the strongest line first.
   - Or build the meditation around one named legend ("A whispered night on Hashima Island"), which pairs
     the format that holds viewers with the mood the project is aiming for.
4. **Write titles that people search for.** Name the legend and the place. Use a colon, no question-style
   titles, and no hashtags in the title (put them in the description).
5. **Re-pull the analytics weekly.** Once about 20 pipeline videos have metrics, weight style packs and
   formats by % watched, and keep some exploration so the choice doesn't lock in.
