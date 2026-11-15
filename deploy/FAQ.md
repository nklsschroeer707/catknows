# Frequently Asked Questions

This page answers what people actually asked in the first weeks of the hosted
service: the questions worth trying, the limits, the price, and where your
password goes. If yours is missing, post it in the community and it will end
up here.

## What can I ask?

Not just single numbers. catknows is not a fixed report: your AI reads the raw
data behind your account, members, posts, comments, DMs, the Discovery charts,
and reasons across all of it at once. That is what sets it apart from every
dashboard: you can ask questions no Skool tool has a column for.

### Where do I start?

First check the connection: ask **“Which Skool communities am I in?”** A list
with your roles and member counts means it works. Then ask something a
dashboard cannot answer.

### Questions worth stealing

- Who joined this month but never posted?
- Who was active in June but has gone quiet since?
- Read the last 50 posts. What do my members actually struggle with?
- Which questions in my community were asked twice but never answered?
- Summarize my unread DMs and draft replies to the urgent ones.
- Which communities rank above mine on Discovery, and what do they charge?
- Write my monthly report: growth, top posts, who needs a check-in.

Each of these crosses at least two kinds of data. That is the trick: the AI
joins what Skool keeps on separate pages.

### Can it write, too?

Yes, when you ask it to. It can draft posts, comments and DMs, and build
Classroom courses. Every write is draft first: your AI shows you the exact
text, and nothing goes live until you say yes.

## What it can’t do

### It only sees what you see

catknows works through your own Skool login. Communities you never joined show
only what Skool shows everyone: the About page and the Discovery listing. There
is no back door, which is the point.

### Video transcripts

Transcripts work for videos hosted on Skool itself. Videos embedded from
YouTube, Loom or Vimeo have no transcript to fetch.

### Freshness

Answers are cached for up to ten minutes, so a number you changed on Skool a
moment ago can lag briefly. Any write clears the cache.

### Very large lists

One call returns at most a few hundred rows. When a list was cut short, the
result says so instead of pretending it saw everything.

### Phones

The streamed Skool login is happiest in a desktop browser. Everything after
that first connection works anywhere your AI does.

## What does it cost?

### Running it yourself

Nothing. The open-source tool on your own machine is free and stays free,
whatever happens to the rest.

### Hosted

$7 a month or $77 a year. You pay inside
[catnose](https://www.skool.com/catnose/about), my community on Skool: Premium
(or VIP) unlocks hosted. Skool takes the payment, so there is no checkout on
catknows.app and I never see your card.

Use the same Skool account there that you connected to catknows. That is how
the server knows it is you: when you join catnose with Premium it checks within
minutes, otherwise once an hour.

### I signed up early

If you made your catknows account before 18 November 2026, 9am Berlin time,
hosted stays free for you. No upgrade needed.

### What if I cancel?

Hosted keeps working until your paid period ends. After that the tools pause
and you get one email. Your account, your login and your connected Skool
session stay, nothing gets deleted. Upgrade again and it works again within the hour.

### What is VIP then?

$67 a month, and it gets you nothing extra on purpose. Hosted is included,
same as Premium. You would be paying so that everyone else does not have to.

### Will the free version quietly get worse?

No. That would defeat the point of having built this.

## Is it safe?

### Where does my password go?

To Skool, nowhere else. Connecting opens a real browser on the server,
streamed to your screen, showing Skool’s own login page. What I keep is the
session cookie that login produces, encrypted at rest.

### What is stored on the server?

Not your Skool data. It is fetched when you ask, used for the answer, and gone.
The server keeps your account email, your encrypted Skool session, and 30 days
of technical usage records. The [privacy policy](PRIVACY.md) lists all of it.

### Can the AI act without me?

No. Reads happen when you ask. Writes are draft first: the exact text is
shown, and nothing is posted, sent or deleted without your explicit yes.

### Where does it run?

On a server in Nürnberg, Germany, under GDPR, with a signed processing
agreement with the hoster. Details are in the [privacy policy](PRIVACY.md) and
the [DPA](DPA.md).

### Can I disconnect?

Any time. Disconnecting deletes your stored session from the server. Logging
out of all devices on Skool kills every session copy anywhere, including mine.

### Is the code really public?

Yes: [github.com/nklsschroeer707/catknows](https://github.com/nklsschroeer707/catknows).
The hosted service runs the code you can read.
