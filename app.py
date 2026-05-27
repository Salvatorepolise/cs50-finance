import os

from cs50 import SQL
from flask import Flask, flash, redirect, render_template, request, session
from flask_session import Session
from werkzeug.security import check_password_hash, generate_password_hash

from helpers import apology, login_required, lookup, usd

# Configure application
app = Flask(__name__)

# Custom filter
app.jinja_env.filters["usd"] = usd

# Configure session to use filesystem (instead of signed cookies)
app.config["SESSION_PERMANENT"] = False
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Configure CS50 Library to use SQLite database
db = SQL("sqlite:///finance.db")


@app.after_request
def after_request(response):
    """Ensure responses aren't cached"""
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Expires"] = 0
    response.headers["Pragma"] = "no-cache"
    return response


@app.route("/")
@login_required
def index():
    """Show portfolio of stocks"""
    user_id = session["user_id"]

    # Get all stocks owned by the user (grouped)
    holdings = db.execute(
        """
        SELECT symbol, SUM(shares) AS total_shares
        FROM transactions
        WHERE user_id = ?
        GROUP BY symbol
        HAVING total_shares > 0
        ORDER BY symbol
        """,
        user_id
    )

    # Get current prices for each holding
    portfolio = []
    stocks_total = 0
    for row in holdings:
        quote = lookup(row["symbol"])
        if quote:
            value = quote["price"] * row["total_shares"]
            stocks_total += value
            portfolio.append({
                "symbol": row["symbol"],
                "name": quote["name"],
                "shares": row["total_shares"],
                "price": quote["price"],
                "total": value
            })

    # Get user's cash balance
    user = db.execute("SELECT cash FROM users WHERE id = ?", user_id)[0]
    cash = user["cash"]
    grand_total = cash + stocks_total

    return render_template(
        "index.html",
        portfolio=portfolio,
        cash=cash,
        grand_total=grand_total
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    """Register user"""
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        confirmation = request.form.get("confirmation")

        # Validate inputs
        if not username:
            return apology("must provide username", 400)
        if not password:
            return apology("must provide password", 400)
        if not confirmation:
            return apology("must confirm password", 400)
        if password != confirmation:
            return apology("passwords do not match", 400)

        # Insert new user (catch duplicate username)
        try:
            user_id = db.execute(
                "INSERT INTO users (username, hash) VALUES(?, ?)",
                username,
                generate_password_hash(password)
            )
        except ValueError:
            return apology("username already exists", 400)

        # Log the user in automatically
        session["user_id"] = user_id
        flash("Registered successfully!")
        return redirect("/")

    else:
        return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Log user in"""
    session.clear()

    if request.method == "POST":
        if not request.form.get("username"):
            return apology("must provide username", 403)
        elif not request.form.get("password"):
            return apology("must provide password", 403)

        rows = db.execute(
            "SELECT * FROM users WHERE username = ?",
            request.form.get("username")
        )

        if len(rows) != 1 or not check_password_hash(
            rows[0]["hash"], request.form.get("password")
        ):
            return apology("invalid username and/or password", 403)

        session["user_id"] = rows[0]["id"]
        return redirect("/")

    else:
        return render_template("login.html")


@app.route("/logout")
def logout():
    """Log user out"""
    session.clear()
    return redirect("/")


@app.route("/quote", methods=["GET", "POST"])
@login_required
def quote():
    """Get stock quote."""
    if request.method == "POST":
        symbol = request.form.get("symbol")
        if not symbol:
            return apology("must provide symbol", 400)

        quote = lookup(symbol)
        if not quote:
            return apology("symbol not found", 400)

        return render_template("quoted.html", quote=quote)

    else:
        return render_template("quote.html")


@app.route("/buy", methods=["GET", "POST"])
@login_required
def buy():
    """Buy shares of stock"""
    if request.method == "POST":
        symbol = request.form.get("symbol")
        shares_str = request.form.get("shares")

        # Validate symbol
        if not symbol:
            return apology("must provide symbol", 400)
        quote = lookup(symbol)
        if not quote:
            return apology("symbol not found", 400)

        # Validate shares
        if not shares_str:
            return apology("must provide number of shares", 400)
        try:
            shares = int(shares_str)
        except ValueError:
            return apology("shares must be a positive integer", 400)
        if shares <= 0:
            return apology("shares must be a positive integer", 400)

        # Check if user can afford it
        user_id = session["user_id"]
        user = db.execute("SELECT cash FROM users WHERE id = ?", user_id)[0]
        cost = quote["price"] * shares

        if cost > user["cash"]:
            return apology("insufficient funds", 400)

        # Execute purchase
        db.execute(
            """
            INSERT INTO transactions (user_id, symbol, shares, price)
            VALUES (?, ?, ?, ?)
            """,
            user_id, quote["symbol"], shares, quote["price"]
        )
        db.execute(
            "UPDATE users SET cash = cash - ? WHERE id = ?",
            cost, user_id
        )

        flash(f"Bought {shares} share(s) of {quote['symbol']}!")
        return redirect("/")

    else:
        return render_template("buy.html")


@app.route("/sell", methods=["GET", "POST"])
@login_required
def sell():
    """Sell shares of stock"""
    user_id = session["user_id"]

    # Get stocks the user owns
    holdings = db.execute(
        """
        SELECT symbol, SUM(shares) AS total_shares
        FROM transactions
        WHERE user_id = ?
        GROUP BY symbol
        HAVING total_shares > 0
        ORDER BY symbol
        """,
        user_id
    )

    if request.method == "POST":
        symbol = request.form.get("symbol")
        shares_str = request.form.get("shares")

        # Validate symbol
        if not symbol:
            return apology("must select a stock", 400)

        # Check user actually owns this stock
        owned = next((h for h in holdings if h["symbol"] == symbol), None)
        if not owned:
            return apology("you do not own this stock", 400)

        # Validate shares
        if not shares_str:
            return apology("must provide number of shares", 400)
        try:
            shares = int(shares_str)
        except ValueError:
            return apology("shares must be a positive integer", 400)
        if shares <= 0:
            return apology("shares must be a positive integer", 400)
        if shares > owned["total_shares"]:
            return apology("not enough shares", 400)

        # Get current price
        quote = lookup(symbol)
        if not quote:
            return apology("could not retrieve stock price", 400)

        proceeds = quote["price"] * shares

        # Record the sale (negative shares)
        db.execute(
            """
            INSERT INTO transactions (user_id, symbol, shares, price)
            VALUES (?, ?, ?, ?)
            """,
            user_id, symbol, -shares, quote["price"]
        )
        db.execute(
            "UPDATE users SET cash = cash + ? WHERE id = ?",
            proceeds, user_id
        )

        flash(f"Sold {shares} share(s) of {symbol}!")
        return redirect("/")

    else:
        return render_template("sell.html", holdings=holdings)


@app.route("/history")
@login_required
def history():
    """Show history of transactions"""
    user_id = session["user_id"]

    transactions = db.execute(
        """
        SELECT symbol, shares, price, transacted_at
        FROM transactions
        WHERE user_id = ?
        ORDER BY transacted_at DESC
        """,
        user_id
    )

    return render_template("history.html", transactions=transactions)


# ── PERSONAL TOUCH 1: Change Password ──────────────────────────────────────
@app.route("/password", methods=["GET", "POST"])
@login_required
def password():
    """Allow user to change their password"""
    if request.method == "POST":
        current = request.form.get("current")
        new_pw  = request.form.get("new_password")
        confirm = request.form.get("confirmation")

        if not current or not new_pw or not confirm:
            return apology("all fields required", 400)
        if new_pw != confirm:
            return apology("new passwords do not match", 400)

        user_id = session["user_id"]
        row = db.execute("SELECT hash FROM users WHERE id = ?", user_id)[0]

        if not check_password_hash(row["hash"], current):
            return apology("current password is incorrect", 403)

        db.execute(
            "UPDATE users SET hash = ? WHERE id = ?",
            generate_password_hash(new_pw),
            user_id
        )
        flash("Password changed successfully!")
        return redirect("/")

    else:
        return render_template("password.html")


# ── PERSONAL TOUCH 2: Add Cash ──────────────────────────────────────────────
@app.route("/add_cash", methods=["GET", "POST"])
@login_required
def add_cash():
    """Allow user to add cash to their account"""
    if request.method == "POST":
        amount_str = request.form.get("amount")
        if not amount_str:
            return apology("must provide amount", 400)
        try:
            amount = float(amount_str)
        except ValueError:
            return apology("invalid amount", 400)
        if amount <= 0:
            return apology("amount must be positive", 400)
        if amount > 50000:
            return apology("maximum deposit is $50,000", 400)

        db.execute(
            "UPDATE users SET cash = cash + ? WHERE id = ?",
            amount, session["user_id"]
        )
        flash(f"Added {usd(amount)} to your account!")
        return redirect("/")

    else:
        return render_template("add_cash.html")
