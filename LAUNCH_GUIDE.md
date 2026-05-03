# LDN DIRECTORY — Data Pipeline & Launch Guide

## How to auto-compile event listings

The site's `EVENTS` array in `index.html` is your flat-file database.
Update it by running the scrapers below, then redeploy to Netlify (automatic on git push).

---

## 1. SOURCES TO SCRAPE

| Source | URL | What you get |
|---|---|---|
| Eventbrite | `https://www.eventbrite.co.uk/d/united-kingdom--london/` | All categories |
| Meetup.com | `https://www.meetup.com/find/?location=London&source=EVENTS` | Fitness, Eco, Wellness |
| London Farmers' Markets | `https://lfm.org.uk/markets/` | Markets |
| Car Boot Junction | `https://www.carbootjunction.com/london` | Markets |
| Visit London | `https://www.visitlondon.com/things-to-do/whats-on` | Festivals, Art, Theatre |
| London borough websites | See list below | Festivals, Eco, Community |
| Resident Advisor | `https://ra.co/events/uk/london` | Wellness (dance) |
| TimeOut London | `https://www.timeout.com/london/things-to-do` | All categories |

### Borough websites to scrape (Zones 2–4)
```
southwark.gov.uk/leisure-and-culture
lewisham.gov.uk/miresidences/parks-and-green-spaces
lambeth.gov.uk/parks-and-open-spaces
wandsworth.gov.uk/events
richmond.gov.uk/leisure-and-culture
ealing.gov.uk/events
haringey.gov.uk/leisure-and-culture
hackney.gov.uk/events
```

---

## 2. SCRAPER SCRIPTS

### Install dependencies
```bash
npm init -y
npm install axios cheerio node-cron dotenv
```

### `scraper/eventbrite.js`
```javascript
const axios = require('axios');

// Use the Eventbrite API (free tier, 1000 calls/day)
// Get your key at: https://www.eventbrite.com/platform/api-keys

const EVENTBRITE_TOKEN = process.env.EVENTBRITE_TOKEN;
const LONDON_BBOX = '51.28,-0.49,51.69,0.23'; // London bounding box

async function fetchEventbriteEvents(category) {
  const categoryMap = {
    theatre:   '105', // Performing arts
    wellness:  '107', // Health
    fitness:   '108', // Sports
    festivals: '113', // Community
    art:       '102', // Visual arts
    create:    '102',
    eco:       '113',
  };

  const url = `https://www.eventbriteapi.com/v3/events/search/`;
  const res = await axios.get(url, {
    headers: { Authorization: `Bearer ${EVENTBRITE_TOKEN}` },
    params: {
      'location.within': '15km',
      'location.address': 'London, UK',
      'categories': categoryMap[category] || '113',
      'sort_by': 'date',
      'expand': 'venue',
    }
  });

  return res.data.events.map(e => ({
    id: parseInt(e.id),
    cat: category,
    title: e.name.text,
    venue: e.venue?.name || 'London',
    area: e.venue?.address?.city || 'London',
    zone: 'south', // derive from postcode lookup (see below)
    date: new Date(e.start.local).toLocaleDateString('en-GB', { weekday: 'long', day: 'numeric', month: 'short' }),
    time: new Date(e.start.local).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
    price: e.is_free ? 'Free' : 'Ticketed',
    free: e.is_free,
    recurrence: 'monthly',
    featured: false,
    desc: e.description?.text?.substring(0, 200) || '',
  }));
}

module.exports = { fetchEventbriteEvents };
```

### `scraper/meetup.js`
```javascript
const axios = require('axios');

// Meetup GraphQL API (no key required for public events)
async function fetchMeetupEvents(keyword) {
  const query = `
    query {
      keywordSearch(
        input: { keyword: "${keyword}", first: 20 }
        filter: { lat: 51.5074, lon: -0.1278, radius: 15 }
      ) {
        edges {
          node {
            result {
              ... on Event {
                id title dateTime duration
                group { name urlname }
                venue { name address city }
                eventUrl
                going
              }
            }
          }
        }
      }
    }
  `;

  const res = await axios.post('https://api.meetup.com/gql', { query }, {
    headers: { 'Content-Type': 'application/json' }
  });

  return res.data.data.keywordSearch.edges.map(({ node: { result: e } }) => ({
    title: e.title,
    venue: e.venue?.name || e.group.name,
    area: e.venue?.city || 'London',
    date: new Date(e.dateTime).toLocaleDateString('en-GB'),
    time: new Date(e.dateTime).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }),
    url: e.eventUrl,
  }));
}

module.exports = { fetchMeetupEvents };
```

### `scraper/farmers-markets.js`
```javascript
const axios = require('axios');
const cheerio = require('cheerio');

async function fetchFarmersMarkets() {
  const res = await axios.get('https://lfm.org.uk/markets/');
  const $ = cheerio.load(res.data);
  const markets = [];

  $('.market-listing').each((i, el) => {
    markets.push({
      cat: 'markets',
      title: $(el).find('.market-name').text().trim(),
      venue: $(el).find('.market-location').text().trim(),
      area: $(el).find('.market-area').text().trim(),
      date: $(el).find('.market-day').text().trim(),
      time: $(el).find('.market-time').text().trim(),
      price: 'Free entry',
      free: true,
      recurrence: 'weekly',
      featured: false,
      desc: $(el).find('.market-desc').text().trim().substring(0, 200),
    });
  });

  return markets;
}

module.exports = { fetchFarmersMarkets };
```

### `scraper/run.js` — Master runner
```javascript
require('dotenv').config();
const fs = require('fs');
const { fetchEventbriteEvents } = require('./eventbrite');
const { fetchMeetupEvents } = require('./meetup');
const { fetchFarmersMarkets } = require('./farmers-markets');

async function run() {
  console.log('🔄 Fetching events...');

  try {
    const [
      theatreEvents,
      wellnessEvents,
      fitnessEvents,
      farmersMarkets,
    ] = await Promise.allSettled([
      fetchEventbriteEvents('theatre'),
      fetchMeetupEvents('yoga pilates sound bath ecstatic dance'),
      fetchMeetupEvents('run club parkrun cycling fitness'),
      fetchFarmersMarkets(),
    ]);

    const allEvents = [
      ...(theatreEvents.value || []),
      ...(wellnessEvents.value || []),
      ...(fitnessEvents.value || []),
      ...(farmersMarkets.value || []),
    ].filter(Boolean);

    // Assign IDs
    const events = allEvents.map((e, i) => ({ id: i + 1000, ...e }));

    // Write to data file
    fs.writeFileSync(
      './data/events.json',
      JSON.stringify(events, null, 2)
    );

    console.log(`✅ ${events.length} events saved to data/events.json`);
    console.log('📤 Commit and push to trigger Netlify deploy');
  } catch (err) {
    console.error('❌ Scrape failed:', err.message);
  }
}

run();
```

### `scraper/inject.js` — Inject JSON into index.html
```javascript
const fs = require('fs');

const events = JSON.parse(fs.readFileSync('./data/events.json', 'utf-8'));
let html = fs.readFileSync('./index.html', 'utf-8');

// Replace the EVENTS array in the HTML
const eventsJson = JSON.stringify(events, null, 2);
html = html.replace(
  /const EVENTS = \[[\s\S]*?\];/,
  `const EVENTS = ${eventsJson};`
);

fs.writeFileSync('./index.html', html);
console.log(`✅ Injected ${events.length} events into index.html`);
```

---

## 3. POSTCODE → ZONE MAPPING

Add this utility to derive zone and direction from a London postcode:

```javascript
function getZoneFromPostcode(postcode) {
  if (!postcode) return { zone: 'south', direction: 'south' };
  const pc = postcode.toUpperCase();

  const northPrefixes = ['N', 'N1', 'N4', 'N7', 'N8', 'N10', 'N15', 'N16', 'N17', 'N19', 'N22', 'NW', 'NW1', 'NW3', 'NW5', 'NW6', 'NW10'];
  const southPrefixes = ['SE', 'SW', 'CR', 'BR', 'SM'];
  const eastPrefixes  = ['E', 'E1', 'E2', 'E3', 'E5', 'E8', 'E9', 'E10', 'E11', 'EC'];
  const westPrefixes  = ['W', 'W4', 'W5', 'W6', 'W13', 'TW', 'UB', 'HA'];

  const prefix = pc.match(/^[A-Z]+\d*/)[0];
  if (northPrefixes.some(p => prefix.startsWith(p))) return { zone: 'north', direction: 'north' };
  if (eastPrefixes.some(p => prefix.startsWith(p))) return { zone: 'east', direction: 'east' };
  if (westPrefixes.some(p => prefix.startsWith(p))) return { zone: 'west', direction: 'west' };
  return { zone: 'south', direction: 'south' };
}
```

---

## 4. AUTOMATE WITH CRON

### `scraper/schedule.js` — Run daily at 6am
```javascript
const cron = require('node-cron');
const { execSync } = require('child_process');

cron.schedule('0 6 * * *', () => {
  console.log('⏰ Daily event refresh...');
  try {
    execSync('node scraper/run.js', { stdio: 'inherit' });
    execSync('node scraper/inject.js', { stdio: 'inherit' });
    execSync('git add -A && git commit -m "auto: daily event refresh" && git push', { stdio: 'inherit' });
    console.log('✅ Pushed — Netlify will auto-deploy');
  } catch (err) {
    console.error('❌ Auto-refresh failed:', err.message);
  }
});

console.log('🕐 Cron scheduler running. Events refresh at 6am daily.');
```

Run on any always-on machine or a free Railway.app / Render.com server:
```bash
node scraper/schedule.js
```

---

## 5. FORMSPREE SETUP (5 minutes)

1. Go to **formspree.io** → Create free account
2. New form → name it "LDN Directory Submissions"
3. Copy your endpoint: `https://formspree.io/f/XXXXXXXX`
4. In `index.html`, find:
   ```html
   <form action="https://formspree.io/f/YOUR_FORM_ID" method="POST">
   ```
   Replace `YOUR_FORM_ID` with your real form ID
5. Submissions land in your Formspree dashboard + email

---

## 6. NETLIFY DEPLOYMENT — FULL STEPS

### Step 1: Create GitHub repo (5 min)
```bash
cd ldn-directory
git init
git add .
git commit -m "initial commit: LDN Directory MVP"
gh repo create ldn-directory --public
git push -u origin main
```

### Step 2: Deploy to Netlify (5 min)
1. Go to **netlify.com** → Log in with GitHub
2. Click **"Add new site" → "Import an existing project"**
3. Choose GitHub → select `ldn-directory`
4. Build settings:
   - Build command: *(leave blank)*
   - Publish directory: `.` (root)
5. Click **Deploy site**

### Step 3: Custom domain (optional, 10 min)
1. In Netlify: **Domain settings → Add custom domain**
2. Type: `ldndirectory.co.uk` (or your choice)
3. Buy on Namecheap (~£10/yr for .co.uk)
4. Point nameservers to Netlify's DNS

### Step 4: Auto-deploy on push
Every `git push` to `main` triggers a new Netlify deploy automatically. Your daily cron job does the push — zero manual work.

---

## 7. ENVIRONMENT VARIABLES

Create `.env` in your project root (never commit this):
```
EVENTBRITE_TOKEN=your_token_here
```

Add to Netlify too:
1. Site settings → Environment variables
2. Add `EVENTBRITE_TOKEN`

---

## 8. LAUNCH DAY CHECKLIST

```
□ Replace YOUR_FORM_ID in index.html with real Formspree ID
□ Test the submit form (check Formspree dashboard)
□ Run: node scraper/run.js && node scraper/inject.js
□ git push → check Netlify deploy succeeded
□ Test on mobile (Chrome DevTools)
□ Share on:
    - Local Facebook groups (Peckham, Dulwich, Clapham, etc.)
    - London subreddits (r/london, r/londonsocialclub)
    - Instagram with #LondonEvents #CommunityLondon
    - Send to local borough newsletters
```

---

## 9. FUTURE V2 IDEAS

- Add Supabase (free) as a real database
- Build an admin panel to approve submitted events
- Email newsletter (Mailchimp free tier)
- Add Google Maps embed per event
- Borough-specific pages (e.g. `/south-london`)
- RSS feed of new events
- PWA / install to home screen
```

---

*LDN DIRECTORY — built for London, by London.*
