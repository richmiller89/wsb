import time
import re
import threading
from flask import Flask, render_template
from flask_socketio import SocketIO
import praw
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

# Initialize Flask and SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = 'your_secret_key'
socketio = SocketIO(app)

# Initialize PRAW with your Reddit credentials
reddit = praw.Reddit(
    client_id='YOUR_CLIENT_ID',
    client_secret='YOUR_CLIENT_SECRET',
    user_agent='YOUR_USER_AGENT'
)

# Initialize the sentiment analyzer
analyzer = SentimentIntensityAnalyzer()

# Global dictionary to hold aggregated ticker data.
# Structure: { "TSLA": { "total_sentiment": X, "count": N, "avg_sentiment": Y }, ... }
tickers_data = {}

def process_submission(submission):
    """
    Process a Reddit submission: extract tickers from the title and selftext,
    run sentiment analysis, and update the global tickers_data.
    """
    # A simple regex to capture tickers like $AAPL, $TSLA, etc.
    pattern = r'\$[A-Z]{2,5}'
    text = submission.title + " " + (submission.selftext or "")
    tickers = re.findall(pattern, text)
    if tickers:
        sentiment = analyzer.polarity_scores(text)['compound']
        for ticker in tickers:
            symbol = ticker.upper().replace('$', '')
            if symbol in tickers_data:
                data = tickers_data[symbol]
                data['total_sentiment'] += sentiment
                data['count'] += 1
                data['avg_sentiment'] = data['total_sentiment'] / data['count']
            else:
                tickers_data[symbol] = {
                    'total_sentiment': sentiment,
                    'count': 1,
                    'avg_sentiment': sentiment
                }

def fetch_reddit_data():
    """
    Fetch the latest posts from r/wallstreetbets and process them.
    """
    global tickers_data
    # For demo purposes, we’ll use the “hot” posts.
    subreddit = reddit.subreddit('wallstreetbets')
    for submission in subreddit.hot(limit=50):
        process_submission(submission)

def background_thread():
    """
    A background thread that refreshes Reddit data every 60 seconds and
    emits updated ticker sentiment data to connected clients.
    """
    global tickers_data
    while True:
        # Reset data on each update (or you can accumulate over time)
        tickers_data = {}
        fetch_reddit_data()
        # Emit the new data to all connected clients.
        socketio.emit('update', tickers_data)
        # Wait a minute before updating again.
        time.sleep(60)

@app.route('/')
def index():
    # The main page (see index.html below)
    return render_template('index.html')

if __name__ == '__main__':
    # Start the background thread that fetches and emits data.
    thread = threading.Thread(target=background_thread)
    thread.daemon = True
    thread.start()
    # Run the Flask-SocketIO app (using eventlet)
    socketio.run(app, debug=True)
