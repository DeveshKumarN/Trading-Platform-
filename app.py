import streamlit as st
import pandas as pd
import time
import random

# ==========================================
# 1. DATABASE SETUP
# ==========================================
try:
    from supabase import create_client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False

# ==========================================
# 2. THE CORE MATCHING ENGINE
# ==========================================
class OrderBook:
    def __init__(self, ticker):
        self.ticker = ticker
        self.bid_book = {}  # Buyers (Bids)
        self.ask_book = {}  # Sellers (Asks)

    def process_order(self, order_id, side, quantity, price, exchange):
        executions = []
        db_deletes = [] # Tracks orders we need to delete from the cloud
        db_updates = [] # Tracks orders we need to update in the cloud
        
        # --- IF THE USER IS BUYING ---
        if side == "BUY":
            # 1. Sort the sellers from lowest price to highest price
            sorted_ask_prices = sorted(self.ask_book.keys())
            
            for ask_price in sorted_ask_prices:
                # Stop if we got all the shares we need, or if the seller is too expensive
                if quantity <= 0 or price < ask_price:
                    break
                
                # Look at all sellers at this specific price
                order_list = self.ask_book[ask_price]
                remaining_orders = []
                
                for resting_order in order_list:
                    if quantity <= 0:
                        remaining_orders.append(resting_order)
                        continue
                    
                    # Figure out how many shares we can actually trade
                    resting_id, resting_qty = resting_order
                    match_qty = min(quantity, resting_qty)
                    
                    # Deduct the traded shares from both the buyer and the seller
                    quantity -= match_qty
                    resting_qty -= match_qty
                    
                    # Record the successful trade (Updated to include date)
                    executions.append({
                        "match_id": exchange.get_next_match_id(),
                        "ticker": self.ticker,
                        "Time": time.strftime('%Y-%m-%d %H:%M:%S'),
                        "Buy Order ID": order_id,
                        "Sell Order ID": resting_id,
                        "Qty": match_qty,
                        "Price": f"${ask_price:.2f}"
                    })
                    
                    # If the seller still has shares left, keep them in the book
                    if resting_qty > 0:
                        remaining_orders.append([resting_id, resting_qty])
                        db_updates.append((resting_id, resting_qty))
                    else:
                        db_deletes.append(resting_id) # Seller is out of shares, remove them
                
                # Update the order book for this price level
                if remaining_orders:
                    self.ask_book[ask_price] = remaining_orders
                else:
                    del self.ask_book[ask_price]
            
            # 2. If the buyer STILL wants more shares, put them in the Bid Book to wait
            if quantity > 0:
                if price not in self.bid_book:
                    self.bid_book[price] = []
                self.bid_book[price].append([order_id, quantity])

        # --- IF THE USER IS SELLING (Exact opposite of buying) ---
        else: 
            # Sort the buyers from highest price to lowest price
            sorted_bid_prices = sorted(self.bid_book.keys(), reverse=True)
            
            for bid_price in sorted_bid_prices:
                if quantity <= 0 or price > bid_price:
                    break
                
                order_list = self.bid_book[bid_price]
                remaining_orders = []
                
                for resting_order in order_list:
                    if quantity <= 0:
                        remaining_orders.append(resting_order)
                        continue
                    
                    resting_id, resting_qty = resting_order
                    match_qty = min(quantity, resting_qty)
                    quantity -= match_qty
                    resting_qty -= match_qty
                    
                    # Record the successful trade (Updated to include date)
                    executions.append({
                        "match_id": exchange.get_next_match_id(),
                        "ticker": self.ticker,
                        "Time": time.strftime('%Y-%m-%d %H:%M:%S'),
                        "Buy Order ID": resting_id,
                        "Sell Order ID": order_id,
                        "Qty": match_qty,
                        "Price": f"${bid_price:.2f}"
                    })
                    
                    if resting_qty > 0:
                        remaining_orders.append([resting_id, resting_qty])
                        db_updates.append((resting_id, resting_qty))
                    else:
                        db_deletes.append(resting_id)
                
                if remaining_orders:
                    self.bid_book[bid_price] = remaining_orders
                else:
                    del self.bid_book[bid_price]
            
            if quantity > 0:
                if price not in self.ask_book:
                    self.ask_book[price] = []
                self.ask_book[price].append([order_id, quantity])
                
        return executions, db_deletes, db_updates, quantity

class Exchange:
    def __init__(self):
        self.tickers = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
        self.books = {ticker: OrderBook(ticker) for ticker in self.tickers}
        self.history = []
        
        # Connect to Supabase Safely
        self.supabase = None
        if SUPABASE_AVAILABLE:
            try:
                self.supabase =create_client("https://gamavocddnnbubrfskkm.supabase.co","eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImdhbWF2b2NkZG5uYnVicmZza2ttIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODMyMzA3MzcsImV4cCI6MjA5ODgwNjczN30.s5giBKE6N1QML9yrFvslpClvwtFHYcDUGgS19mTRexY")
            except Exception:
               except Exception as e:
            st.error(f"DEBUG ERROR: {e}")

    def get_next_match_id(self):
        return int(time.time() * 1000) + random.randint(1, 1000)

    def submit_order(self, ticker, order_id, side, quantity, price):
        # 1. Run the math engine
        matches, db_deletes, db_updates, remaining_qty = self.books[ticker].process_order(order_id, side, quantity, price, self)
        
        if matches:
            self.history.extend(matches)
            
        # 2. Push the math results to the Cloud Database
        if self.supabase:
            try:
                # Save new trades
                if matches:
                    self.supabase.table("trades").insert(matches).execute()
                # Delete empty orders
                for d_id in db_deletes:
                    self.supabase.table("orders").delete().eq("order_id", d_id).execute()
                # Update partially filled orders
                for u_id, u_qty in db_updates:
                    self.supabase.table("orders").update({"quantity": u_qty}).eq("order_id", u_id).execute()
                # Save the new resting order
                if remaining_qty > 0:
                    self.supabase.table("orders").insert({
                        "order_id": order_id, "ticker": ticker, "side": side, 
                        "quantity": remaining_qty, "price": price
                    }).execute()
            except Exception as e:
                st.sidebar.error("Database Sync Error")
                
        return matches

    def cancel_order(self, ticker, side, price, order_id):
        """Removes a specific order from the local book and the cloud database."""
        book = self.books[ticker]
        target_book = book.bid_book if side == "BUY" else book.ask_book
        
        if price in target_book:
            original_len = len(target_book[price])
            # Keep all orders EXCEPT the one we want to cancel
            target_book[price] = [o for o in target_book[price] if o[0] != order_id]
            
            # If no orders left at this price, remove the price level entirely
            if len(target_book[price]) == 0:
                del target_book[price]
            
            # Sync to Database
            if self.supabase and original_len != len(target_book.get(price, [])):
                try:
                    self.supabase.table("orders").delete().eq("order_id", order_id).execute()
                except Exception as e:
                    st.error(f"DB Cancel Error: {e}")
            return True
        return False

# ==========================================
# 3. HELPER FUNCTIONS
# ==========================================
def sync_market_data(exchange):
    """Pulls the latest data from the cloud when you open the app."""
    if exchange.supabase:
        try:
            exchange.history = exchange.supabase.table("trades").select("*").execute().data
            orders = exchange.supabase.table("orders").select("*").execute().data
            
            # Clear local books
            for ticker in exchange.tickers:
                exchange.books[ticker].bid_book = {}
                exchange.books[ticker].ask_book = {}
                
            # Refill local books from cloud data
            for o in orders:
                t, p = o['ticker'], float(o['price'])
                if o['side'] == 'BUY':
                    if p not in exchange.books[t].bid_book: exchange.books[t].bid_book[p] = []
                    exchange.books[t].bid_book[p].append([o['order_id'], o['quantity']])
                else:
                    if p not in exchange.books[t].ask_book: exchange.books[t].ask_book[p] = []
                    exchange.books[t].ask_book[p].append([o['order_id'], o['quantity']])
            return
        except Exception:
            pass
            
    # Dummy Local Data (Only runs if Supabase fails or isn't set up)
    for ticker in exchange.tickers:
        exchange.submit_order(ticker, f"SYS_S1_{ticker}", "SELL", 500, 101.0)
        exchange.submit_order(ticker, f"SYS_B1_{ticker}", "BUY", 500, 99.0)
    exchange.history = []

def format_book(book_dict, reverse_sort):
    """Converts the Order Book dictionary into a clean Pandas Table."""
    if not book_dict:
        return pd.DataFrame(columns=["Price", "Qty"])
    
    data = []
    for price in sorted(book_dict.keys(), reverse=reverse_sort):
        total_qty = sum(order[1] for order in book_dict[price])
        data.append({"Price": price, "Qty": total_qty})
        
    return pd.DataFrame(data)

# ==========================================
# 4. STREAMLIT USER INTERFACE
# ==========================================
st.set_page_config(page_title="Trading Platform", layout="wide")

# Initialize Exchange
if "exchange" not in st.session_state:
    st.session_state.exchange = Exchange()
    sync_market_data(st.session_state.exchange)

exc = st.session_state.exchange

# --- LEFT SIDEBAR ---
st.sidebar.title("Watchlist")
selected_ticker = st.sidebar.radio("Select Asset", exc.tickers)

st.sidebar.markdown("---")
if exc.supabase:
    st.sidebar.success("🟢 Cloud Mode Active")
else:
    st.sidebar.warning("🟡 Local Mode Active")

if st.sidebar.button("🔄 Refresh Data", use_container_width=True):
    sync_market_data(exc)
    st.rerun()

# --- MAIN SCREEN ---
book = exc.books[selected_ticker]
best_bid = max(book.bid_book.keys()) if book.bid_book else 0.0
best_ask = min(book.ask_book.keys()) if book.ask_book else 0.0

# Calculate Live Price (LTP) from the most recent executed trade for the selected ticker
ticker_trades = [trade for trade in exc.history if trade.get('ticker') == selected_ticker]
if ticker_trades:
    # Get the last recorded trade and strip the '$' to treat it as a float
    latest_trade = ticker_trades[-1]
    last_price = float(latest_trade['Price'].replace('$', ''))
else:
    # Fallback if no trades have been executed yet
    last_price = best_ask if best_ask else (best_bid if best_bid else 0.0)

# Split screen: Left (Data) and Right (Order Pad)
col_data, col_order = st.columns([2, 1], gap="large")

with col_data:
    st.header(f"{selected_ticker} Market Data")
    
    # NEW: Display the Live Price prominently using st.metric cards
    m1, m2, m3 = st.columns(3)
    m1.metric(label="Live Price (LTP)", value=f"${last_price:.2f}")
    m2.metric(label="Best Bid", value=f"${best_bid:.2f}" if best_bid else "N/A")
    m3.metric(label="Best Ask", value=f"${best_ask:.2f}" if best_ask else "N/A")
    st.markdown("---")

    # NEW: Added "My Orders" Tab
    tab_depth, tab_trades, tab_my_orders = st.tabs(["Market Depth", "Global Trades", "My Orders"])
    
    with tab_depth:
        col_bids, col_asks = st.columns(2)
        with col_bids:
            st.markdown("### BIDS (Buyers)")
            df_bids = format_book(book.bid_book, reverse_sort=True)
            st.dataframe(df_bids, use_container_width=True, hide_index=True)

        with col_asks:
            st.markdown("### ASKS (Sellers)")
            df_asks = format_book(book.ask_book, reverse_sort=False)
            st.dataframe(df_asks, use_container_width=True, hide_index=True)
                
    with tab_trades:
        if exc.history:
            df_history = pd.DataFrame(exc.history[::-1])
            # Rename the "Time" column from the database to explicitly say "Date & Time"
            df_history = df_history.rename(columns={
                "match_id": "Match ID", 
                "ticker": "Asset", 
                "Time": "Date & Time"
            })
            st.dataframe(df_history, use_container_width=True, hide_index=True)
        else:
            st.info("No trades executed yet.")
            
    with tab_my_orders:
        st.subheader("Your Pending Orders")
        # 1. Gather all pending orders starting with "USR_" across ALL tickers
        pending_orders = []
        for t in exc.tickers:
            for p, orders in exc.books[t].bid_book.items():
                for o in orders:
                    if str(o[0]).startswith("USR_"):
                        pending_orders.append({"Order ID": o[0], "Asset": t, "Side": "BUY", "Qty": o[1], "Price": p})
            for p, orders in exc.books[t].ask_book.items():
                for o in orders:
                    if str(o[0]).startswith("USR_"):
                        pending_orders.append({"Order ID": o[0], "Asset": t, "Side": "SELL", "Qty": o[1], "Price": p})
        
        if pending_orders:
            st.dataframe(pd.DataFrame(pending_orders), use_container_width=True, hide_index=True)
            
            st.markdown("---")
            st.subheader("Modify / Cancel Order")
            # Select Order to Manage
            manage_id = st.selectbox("Select an Order ID to manage:", [o["Order ID"] for o in pending_orders])
            selected_order = next(o for o in pending_orders if o["Order ID"] == manage_id)
            
            m_col1, m_col2, m_col3 = st.columns(3)
            with m_col1:
                action = st.radio("Action", ["Modify Order", "Cancel Order"])
            with m_col2:
                new_qty = st.number_input("New Qty", min_value=1, step=10, value=selected_order["Qty"], disabled=(action=="Cancel Order"))
            with m_col3:
                new_price = st.number_input("New Price", min_value=1.0, step=0.5, value=float(selected_order["Price"]), disabled=(action=="Cancel Order"))
            
            if st.button("Confirm Action", type="primary"):
                # Always cancel the old order first
                exc.cancel_order(selected_order["Asset"], selected_order["Side"], selected_order["Price"], selected_order["Order ID"])
                
                if action == "Modify Order":
                    # Place a new order with the updated details
                    new_id = f"USR_{int(time.time() * 1000)}"
                    exc.submit_order(selected_order["Asset"], new_id, selected_order["Side"], new_qty, new_price)
                    st.success(f"Order successfully modified! New Order ID is {new_id}")
                else:
                    st.success(f"Order {manage_id} successfully cancelled.")
                
                time.sleep(1) # Give user a second to read success message
                st.rerun() # Refresh the page to show updated book
                
        else:
            st.info("You have no pending orders in the market.")
            
        st.markdown("---")
        st.subheader("Your Executed Trades")
        # 2. Gather all executed trades that involve a "USR_" order
        executed_orders = []
        for trade in exc.history:
            if str(trade.get("Buy Order ID", "")).startswith("USR_"):
                executed_orders.append({"Match ID": trade["match_id"], "Asset": trade["ticker"], "Side": "BUY", "Qty": trade["Qty"], "Price": trade["Price"], "Date & Time": trade["Time"]})
            if str(trade.get("Sell Order ID", "")).startswith("USR_"):
                executed_orders.append({"Match ID": trade["match_id"], "Asset": trade["ticker"], "Side": "SELL", "Qty": trade["Qty"], "Price": trade["Price"], "Date & Time": trade["Time"]})
                
        if executed_orders:
            st.dataframe(pd.DataFrame(executed_orders[::-1]), use_container_width=True, hide_index=True)
        else:
            st.info("You have no executed trades yet.")

with col_order:
    st.header("Order Pad")
    
    # Helper to process UI clicks
    def handle_order(side, qty, price):
        order_id = f"USR_{int(time.time() * 1000)}"
        matches = exc.submit_order(selected_ticker, order_id, side, qty, price)
        if matches:
            st.success(f"Executed {len(matches)} trade(s)!")
        else:
            st.info(f"Order placed in {side} book.")
        time.sleep(0.5) 
        st.rerun()

    tab_buy, tab_sell = st.tabs(["BUY", "SELL"])
    
    with tab_buy:
        qty = st.number_input("Qty", min_value=1, step=10, value=100, key="bq")
        price = st.number_input("Price", min_value=1.0, step=0.5, value=float(last_price if last_price else 100.0), key="bp")
        if st.button("Submit BUY Order", type="primary", use_container_width=True):
            handle_order("BUY", qty, price)

    with tab_sell:
        qty = st.number_input("Qty", min_value=1, step=10, value=100, key="sq")
        price = st.number_input("Price", min_value=1.0, step=0.5, value=float(last_price if last_price else 100.0), key="sp")
        if st.button("Submit SELL Order", use_container_width=True):
            handle_order("SELL", qty, price)
