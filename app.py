import os
import re
import time
import threading
import logging
from datetime import datetime, timedelta
from collections import defaultdict

from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import praw
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
import yfinance as yf

# Configure logging with DEBUG level
logging.basicConfig(
    level=logging.DEBUG,  # Changed from INFO to DEBUG
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logging.getLogger('urllib3').setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

# Global excluded words for ticker extraction
EXCLUDED_WORDS = {
    # Common English words
    'A', 'I', 'AT', 'BE', 'DO', 'GO', 'IN', 'IS', 'IT', 'ON', 'OR', 'SO', 'TO', 'UP',
    # Reddit/WSB specific
    'WSB', 'DD', 'YOLO', 'FOMO', 'IMO', 'TBH', 'TLDR', 'AFAIK', 'ELI5', 'TIL', 'DM',
    # Business/Finance terms
    'CEO', 'CFO', 'IPO', 'EPS', 'ATH', 'ROI', 'YOY', 'P/E', 'USD', 'GDP', 'SEC',
    # Technical terms
    'CPU', 'GPU', 'API', 'RAM', 'ROM', 'USB', 'SQL', 'CSS', 'HTML',
}

class SentimentTracker:
    def __init__(self):
        # This regex looks for a $ followed by 1–5 uppercase letters (with an optional dot and one more letter)
        self.dollar_ticker_re = re.compile(r'\$([A-Z]{1,5}(?:\.[A-Z])?)\b')
        # This regex finds any standalone word consisting of 1–5 uppercase letters (optionally with dot+letter)
        self.word_ticker_re = re.compile(r'\b[A-Z]{1,5}(?:\.[A-Z])?\b')
        
        self.bullish_re = re.compile(r'\b(buy|long|calls|moon|rocket|bullish|pump|growth)\b', re.IGNORECASE)
        self.bearish_re = re.compile(r'\b(sell|short|puts|bearish|dump|crash|drop)\b', re.IGNORECASE)
        
        try:
            logger.info("Initializing Reddit client...")
            self.reddit = praw.Reddit(
                client_id='cv3w5se-tfdyVkoUZQNqTw',
                client_secret='k8cW_vbnQh07kd4CiMU_uGYE3UBtQg',
                user_agent='script:wsb-sentiment:v1.0 (by u/Leather_Impact_4366)'
            )
            self.reddit.read_only = True
            logger.info("Reddit client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Reddit client: {e}")
            raise

        self.vader = SentimentIntensityAnalyzer()
        
        self.sentiment_data = defaultdict(lambda: {
            'positive': 0,
            'neutral': 0,
            'negative': 0,
            'total_mentions': 0,
            'last_updated': None,
            'first_seen': None
        })
        
        self.verified_tickers = set()
        self.invalid_tickers = set()
        self.last_cache_cleanup = datetime.now()
        
        self.time_windows = {
            '12H': timedelta(hours=12),
            '1D': timedelta(days=1),
            '1W': timedelta(days=7),
            '1M': timedelta(days=30)
        }

    def verify_ticker(self, ticker):
        """Verify if a string is a valid stock ticker using yfinance history data."""
        ticker = ticker.strip().upper()
        
        # Quick checks
        if ticker in self.verified_tickers:
            logger.debug(f"Ticker {ticker} found in verified cache")
            return True
        if ticker in self.invalid_tickers or ticker in EXCLUDED_WORDS:
            logger.debug(f"Ticker {ticker} excluded: {'in invalid_tickers' if ticker in self.invalid_tickers else 'in EXCLUDED_WORDS'}")
            return False
        
        # Remove any dot for length check
        ticker_without_dot = ticker.replace('.', '')
        if len(ticker_without_dot) > 5 or not ticker_without_dot.isalpha():
            logger.debug(f"Ticker {ticker} rejected: {'length > 5' if len(ticker_without_dot) > 5 else 'not alphabetic'}")
            return False

        try:
            logger.info(f"Attempting to verify ticker: {ticker}")
            stock = yf.Ticker(ticker)
            # Instead of relying on stock.info, try fetching historical data over the past 5 days.
            hist = stock.history(period="5d")
            if not hist.empty:
                logger.info(f"Verified valid ticker: {ticker}")
                self.verified_tickers.add(ticker)
                return True
            
            logger.debug(f"Ticker {ticker} rejected: empty history data")
            self.invalid_tickers.add(ticker)
            return False

        except Exception as e:
            logger.debug(f"yfinance verification failed for {ticker}: {e}")
            self.invalid_tickers.add(ticker)
            return False

    def analyze_sentiment(self, text):
        """Analyze sentiment with context awareness."""
        scores = self.vader.polarity_scores(text)
        bull_count = len(self.bullish_re.findall(text))
        bear_count = len(self.bearish_re.findall(text))
        
        adjusted_score = scores['compound']
        if bull_count > bear_count:
            adjusted_score = min(1.0, adjusted_score + 0.1 * (bull_count - bear_count))
        elif bear_count > bull_count:
            adjusted_score = max(-1.0, adjusted_score - 0.1 * (bear_count - bull_count))
        
        if adjusted_score >= 0.2:
            return 'positive'
        elif adjusted_score <= -0.2:
            return 'negative'
        return 'neutral'

    def extract_tickers(self, text):
        """Extract potential stock tickers from text with improved pattern matching."""
        # Get high-confidence tickers (with $)
        dollar_tickers = self.dollar_ticker_re.findall(text)
        # Get all standalone uppercase words that could be tickers
        word_tickers = self.word_ticker_re.findall(text)
        # Combine, giving priority to those with $
        potential_tickers = set(dollar_tickers + [t for t in word_tickers if t not in dollar_tickers and t not in EXCLUDED_WORDS])
        
        logger.debug(f"Found potential tickers: {potential_tickers}")
        verified = [t for t in potential_tickers if self.verify_ticker(t)]
        
        if verified:
            logger.debug(f"Extracted verified tickers: {verified} from text: {text[:100]}...")
        return verified

    def process_submission(self, submission):
        """Process a Reddit submission with improved ticker detection and sentiment analysis."""
        text = f"{submission.title} {submission.selftext}"
        tickers = self.extract_tickers(text)
        
        if tickers:
            sentiment = self.analyze_sentiment(text)
            timestamp = datetime.fromtimestamp(submission.created_utc)
            logger.debug(f"Processing submission with tickers: {tickers}, sentiment: {sentiment}")
            
            for ticker in tickers:
                ticker = ticker.upper()
                if not self.sentiment_data[ticker]['first_seen']:
                    self.sentiment_data[ticker]['first_seen'] = timestamp
                
                self.sentiment_data[ticker][sentiment] += 1
                self.sentiment_data[ticker]['total_mentions'] += 1
                self.sentiment_data[ticker]['last_updated'] = timestamp

    def fetch_reddit_data(self):
        """Fetch and analyze Reddit data with improved coverage."""
        try:
            subreddit = self.reddit.subreddit('wallstreetbets')
            posts = []
            
            logger.info("Attempting to fetch Reddit posts...")
            
            try:
                hot_posts = list(subreddit.hot(limit=200))
                logger.info(f"Fetched {len(hot_posts)} hot posts")
                posts.extend(hot_posts)
                
                new_posts = list(subreddit.new(limit=200))
                logger.info(f"Fetched {len(new_posts)} new posts")
                posts.extend(new_posts)
                
                rising_posts = list(subreddit.rising(limit=100))
                logger.info(f"Fetched {len(rising_posts)} rising posts")
                posts.extend(rising_posts)
                
            except Exception as e:
                logger.warning(f"Error fetching some posts: {e}")
            
            if not posts:
                logger.warning("No posts fetched")
                return False
            
            logger.info(f"Total posts fetched: {len(posts)}")
            self.sentiment_data.clear()
            
            processed_posts = 0
            tickers_found = set()
            
            for submission in posts:
                text = f"{submission.title} {submission.selftext}"
                tickers = self.extract_tickers(text)
                if tickers:
                    processed_posts += 1
                    tickers_found.update(tickers)
                    logger.info(f"Found tickers {tickers} in post: {submission.title[:50]}...")
                
                self.process_submission(submission)
            
            logger.info(f"Processed {processed_posts} posts containing tickers")
            logger.info(f"Total unique tickers found: {len(tickers_found)}")
            logger.info(f"Tickers found: {sorted(list(tickers_found))}")
            
            if (datetime.now() - self.last_cache_cleanup) > timedelta(hours=1):
                self.verified_tickers.clear()
                self.invalid_tickers.clear()
                self.last_cache_cleanup = datetime.now()
            
            return True
            
        except Exception as e:
            logger.error(f"Error in fetch_reddit_data: {e}")
            return False

    def get_time_window_data(self, window='12H'):
        """Get sentiment data for a specific time window."""
        cutoff = datetime.now() - self.time_windows[window]
        window_data = {}
        
        for ticker, data in self.sentiment_data.items():
            if data['last_updated'] and data['last_updated'] > cutoff:
                window_data[ticker] = {
                    'mentions': {
                        'positive': data['positive'],
                        'neutral': data['neutral'],
                        'negative': data['negative']
                    },
                    'total_mentions': data['total_mentions'],
                    'last_updated': data['last_updated'].isoformat()
                }
        
        return window_data

# Initialize Flask and SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = 'k8cW_vbnQh07kd4CiMU_uGYE3UBtQg'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Create tracker instance
tracker = SentimentTracker()

def background_thread():
    """Background task to fetch and emit data every 5 minutes."""
    while True:
        logger.info("Starting data refresh cycle")
        if tracker.fetch_reddit_data():
            data = {
                window: tracker.get_time_window_data(window)
                for window in tracker.time_windows.keys()
            }
            socketio.emit('data_update', data)
            logger.info(f"Data emitted for {len(tracker.sentiment_data)} tickers")
        
        time.sleep(300)

@socketio.on('connect')
def handle_connect(auth):
    """Handle client connection."""
    logger.info("Client connected")
    data = {
        window: tracker.get_time_window_data(window)
        for window in tracker.time_windows.keys()
    }
    emit('data_update', data)

@socketio.on('disconnect')
def handle_disconnect():
    """Handle client disconnection."""
    logger.info("Client disconnected")

@app.route('/')
def index():
    """Serve the main page."""
    return render_template('index.html')

if __name__ == '__main__':
    thread = threading.Thread(target=background_thread, daemon=True)
    thread.start()
    
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, use_reloader=False)
