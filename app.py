import os
import re
import time
import threading

from flask import Flask, render_template
from flask_socketio import SocketIO
import praw
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

##############################################################################
# Configuration
##############################################################################

# For real production, store secrets in environment variables or a .env file!
FLASK_SECRET_KEY = 'SECRET_KEY'
REDDIT_CLIENT_ID = 'REDDIT_CLIENT_ID'
REDDIT_CLIENT_SECRET = 'SECRET_ID'
USER_AGENT = 'script:wsb-sentiment:v1.0 (by u/YourRedditUsername)'

# Flask app config
app = Flask(__name__)
app.config['SECRET_KEY'] = FLASK_SECRET_KEY
socketio = SocketIO(app, cors_allowed_origins="*")

##############################################################################
# Reddit + Sentiment Analyzer Setup
##############################################################################

reddit = praw.Reddit(
    client_id=REDDIT_CLIENT_ID,
    client_secret=REDDIT_CLIENT_SECRET,
    user_agent=USER_AGENT
)
reddit.read_only = True

analyzer = SentimentIntensityAnalyzer()

##############################################################################
# Global Data and Constants
##############################################################################

tickers_data = {}
SUBREDDIT_NAME = 'wallstreetbets'
REFRESH_INTERVAL = 60

# Expanded regex:
#  - \$[A-Z]{2,5} matches $TSLA, $AAPL, ...
#  - \b[A-Z]{2,5}\b matches plain AAPL, TSLA, etc.
TICKER_REGEX = re.compile(r'(?:\$[A-Z]{2,5}|\b[A-Z]{2,5}\b)')

##############################################################################
# Core Logic
##############################################################################

def process_submission(submission):
    """
    Extract tickers from a single Reddit submission's title/selftext,
    compute sentiment, and update the global tickers_data dictionary.
    """
    global tickers_data

    text = submission.title + " " + (submission.selftext or "")

    # Find all matches (both $TSLA and TSLA).
    matches = TICKER_REGEX.findall(text)
    # Debug print:
    print(f"[DEBUG] Title: {submission.title[:60]!r}... Tickers found: {matches}")

    if matches:
        sentiment = analyzer.polarity_scores(text)['compound']
        for raw_ticker in matches:
            # Remove $ if present and ensure uppercase
            symbol = raw_ticker.replace('$', '').upper()

            if symbol not in tickers_data:
                tickers_data[symbol] = {
                    'total_sentiment': 0.0,
                    'count': 0,
                    'avg_sentiment': 0.0
                }
            tickers_data[symbol]['total_sentiment'] += sentiment
            tickers_data[symbol]['count'] += 1
            c = tickers_data[symbol]['count']
            tot = tickers_data[symbol]['total_sentiment']
            tickers_data[symbol]['avg_sentiment'] = tot / c

def fetch_reddit_data():
    """
    Fetch the latest hot posts from r/wallstreetbets and process each submission.
    """
    subreddit = reddit.subreddit(SUBREDDIT_NAME)
    # Debug: Let’s print out how many we're iterating over
    print(f"[DEBUG] Fetching 'hot' posts from r/{SUBREDDIT_NAME}")
    for submission in subreddit.hot(limit=50):
        # Debug: Print just the title of each post:
        # print(f"Fetching post: {submission.title}")
        process_submission(submission)

def background_thread():
    """
    Periodically refresh the Reddit data and emit the updated sentiment
    to all connected SocketIO clients.
    """
    global tickers_data
    while True:
        # Reset data each cycle
        tickers_data = {}
        
        print("[DEBUG] Starting data fetch...")
        try:
            fetch_reddit_data()
        except Exception as e:
            print(f"[ERROR] Could not fetch from Reddit: {e}")

        # If after fetching from WSB we still have no data, let's add a small sample
        # so the chart won't be blank. Remove this if you only want real data.
        if not tickers_data:
            print("[DEBUG] No tickers found - adding sample data.")
            tickers_data = {
                "GME": {"total_sentiment": 3.5, "count": 2, "avg_sentiment": 1.75},
                "TSLA": {"total_sentiment": -1.0, "count": 1, "avg_sentiment": -1.0},
                "AAPL": {"total_sentiment": 0.2, "count": 2, "avg_sentiment": 0.1},
            }

        # Emit to clients
        socketio.emit('update', tickers_data)
        print("[INFO] Emitted updated ticker data to clients.\n")

        # Wait until next refresh
        time.sleep(REFRESH_INTERVAL)

##############################################################################
# Flask Routes
##############################################################################

@app.route('/')
def index():
    return render_template('index.html')

##############################################################################
# Main Entry Point
##############################################################################

if __name__ == '__main__':
    thread = threading.Thread(target=background_thread, daemon=True)
    thread.start()
    socketio.run(app, debug=True)
