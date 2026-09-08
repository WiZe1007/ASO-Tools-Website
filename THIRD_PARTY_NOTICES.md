# Third-party notices

These notices retain the provenance of successive design iterations. The final
Alpine refinement sections below describe the latest photograph and motion
references; descriptions of earlier appearances are historical records.

An earlier optical backdrop implementation adapted the public CodePen **GitHub Profile** by
**Abdughafur Khujzoda**:
https://codepen.io/Abdughafur-Khujzoda/pen/jEyVvqK

The navigation capsule and theme icon treatment originated from the public CodePen **glassy style nav** by **Leon Lin
(LeonLinBuild)**:
https://codepen.io/LeonLinBuild/pen/emdgRJj

The current workspace composition, navigation, fine rims and restrained blur are inspired
by Jake Bogan’s public CodePen **News Feed Challenge**:
https://codepen.io/jakebogan01/pen/PwbpgZX

SVG distortion, glossy reflections and panel spotlights from the earlier
iteration have been removed.

Public Pens are provided under the MIT License, as documented by CodePen:
https://blog.codepen.io/docs/pens/licensing/

The following license applies to these adapted portions. Reference photographs,
avatars, advertisements and CodePen UI are not included in the site assets.

## MIT License

Copyright (c) Abdughafur Khujzoda
Copyright (c) Leon Lin (LeonLinBuild)
Copyright (c) Jake Bogan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
THE SOFTWARE.

## Grand Teton background photograph

Photo by Andreas Miller, Grand Teton National Park, Wyoming, USA (October 2023).
Source: https://unsplash.com/photos/a-river-running-through-a-lush-green-forest-T-nH_BG3860
License: https://unsplash.com/license

Local assets: `static/img/grand-teton-snake-river.jpg` (2560px) and
`static/img/grand-teton-snake-river-mobile.jpg` (1200px). Both are size-optimized
copies of the same photograph downloaded from Unsplash's image CDN. No API,
tracking script or remote image request is required when the site is viewed.
This photo is used as part of the website, not distributed as a standalone
stock-photo product.

## Flownest and user-supplied screenshot references

The earlier home-page landscape composition and serif emphasis
were inspired by **Flownest Studio - Yoga Website**, published by Permadi Satria
Dewanto for Plainthing Studio and crediting designer Nisa:
https://dribbble.com/shots/26393477-Flownest-Studio-Yoga-Website

The user supplied a screenshot of this hero as an earlier home-page reference.
A second user-supplied screenshot informs the circular-logo / capsule-menu
composition. WWA uses its own logo, links, copy and independently written CSS.
No Flownest photographs, video, branding, copy or source code are distributed.


## Current SaaS and naturetech visual references

The user supplied the following designs as visual direction. WWA's current
centered typography, cool dark palette, restrained glass tool directory and
nature background were independently implemented from these references.
No source code, product imagery, logos, videos, metrics or customer content from
these designs is bundled with WWA.

- Datacore — Outcrowd: https://dribbble.com/shots/27043865-Datacore-Website-for-an-API-First-AI-Platform
- Resq.io — Outcrowd: https://dribbble.com/shots/26742281-Website-for-an-Incident-Management-Platform
- Projexion — Outcrowd: https://dribbble.com/shots/26513848-Website-for-Project-Management-and-Team-Collaboration
- Hoosty — Nexila Agency: https://dribbble.com/shots/26262684-Hoosty-AI-Business-Website-for-Smart-Scalable-Digital-Solutions
- SyncDepth — Halo UI/UX for HALO LAB: https://dribbble.com/shots/27635100-Website-for-a-Naturetech-Product-SyncDepth

## User-supplied Stellar / Skyscape screenshots

The current appearance is independently implemented from eight screenshots
provided by the user (8 September 2026, 15:38–15:39), showing Stellar and
Skyscape glass interfaces. The user chose WWA Tools copy and functional content
in the reproduced composition. The original screenshots, product logos,
customer portraits, flight data and testimonial text are not bundled.

`static/img/stellar-ribbon.png` is an image-generated standalone recreation of
the purple/blue abstract artwork in the supplied visual reference. Other
visual elements are code-native glass surfaces, geometric linework, existing
WWA icons and interactive range controls. The photo credits above document
an earlier iteration; the photograph is no longer displayed by this design.

## User-supplied Alpine sunset photograph

The user supplied `sam-ferrara-1527pjeb6jg-unsplash.jpg` on 8 September 2026
and requested that it become the background. Attribution from the supplied
filename: Sam Ferrara / Unsplash.

The image is copied unchanged into `static/img/alpine-sunset.jpg`. Cropping,
scaling and overlays are display treatments in CSS. The photograph is served
locally as part of the WWA Tools website, with no remote image dependency.
This record identifies the user-supplied asset; it does not claim ownership
of the photograph or grant a new license to it.

## React Bits public design and interaction references

The Alpine refinement uses independently written CSS and native JavaScript
informed by publicly documented layout and interaction patterns:

- Hero 16: https://pro.reactbits.dev/docs/blocks/hero-section/hero-16
- Navigation 12: https://pro.reactbits.dev/docs/blocks/navigation/navigation-12
- Staggered Text: https://pro.reactbits.dev/docs/components/staggered-text
- Parallax Cards: https://pro.reactbits.dev/docs/components/parallax-cards
- Spotlight Card: https://reactbits.dev/components/spotlight-card
- Magnet: https://reactbits.dev/animations/magnet

React Bits is created by David Haz. The referenced photographic hero,
floating navigation, text entrances, pointer depth, card highlights and
button feedback are visual and behavioral inspiration. No paid React Bits
Pro source code, templates, registry packages or preview assets are copied
or installed. No React Bits library dependency is added to WWA Tools.

The Pro product has its own commercial terms:
https://pro.reactbits.dev/license

The separate free React Bits repository uses MIT + Commons Clause:
https://github.com/DavidHDev/react-bits/blob/main/LICENSE.md

That license is recorded for reference and is not presented as a license
for the paid Pro product. The native refinement is an original implementation
for this site, not a redistributed or ported React Bits component package.


## Splash Cursor integration (September 2026)

`static/js/splash-cursor.js` adapts React Bits' SplashCursor simulation by David Haz
for this WWA Tools application. Source:
https://github.com/DavidHDev/react-bits/blob/main/src/content/Animations/SplashCursor/SplashCursor.jsx

The component's React wrapper is replaced by native page lifecycle handling.
WWA adds bounded rendering resolution, lazy initialization, idle suspension,
GPU resource disposal, reduced-motion/pause integration and transparent layering.
The simulation is included as part of this website, not as a standalone component
package. Its full MIT + Commons Clause notice is in `static/licenses/react-bits.txt`.


## User-supplied mountain video (September 2026)

The user supplied `12076135_3840_2160_60fps.mp4` and requested it as the website
background. Local derivatives are `static/video/mountain-mist-desktop.mp4` and
`mountain-mist-mobile.mp4`; the adjacent WebP posters are first-frame extracts.
The derivatives are resized, frame-rate reduced, cropped for phones, and
re-encoded for web delivery. The original supplied file is not modified or
included in the website bundle. This records asset provenance and does not
assert ownership or grant a new license to the footage.
