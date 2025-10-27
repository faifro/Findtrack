
import os
from datetime import datetime, date, timedelta
from collections import defaultdict
from io import StringIO, BytesIO

from flask import Flask, render_template, request, redirect, url_for, send_file, flash
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func, case
from werkzeug.utils import secure_filename

# --- App ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'dev-secret'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///fintrack.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

db = SQLAlchemy(app)


@app.template_filter('peso')
def peso_format(value):
    try:
        formatted = f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "$0.00"
    # Convert US thousands/decimal separators to a more familiar Spanish style
    formatted = formatted.replace(',', 'X').replace('.', ',').replace('X', '.')
    return f"${formatted}"

# --- Models ---
class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)

class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    direction = db.Column(db.String(10), nullable=False)  # 'in' or 'out'
    amount = db.Column(db.Float, nullable=False, default=0.0)
    description = db.Column(db.String(500), default="")
    scope = db.Column(db.String(20), default="personal")  # personal / negocio
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'))
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))
    attachment = db.Column(db.String(255), default=None)

    account = db.relationship('Account', lazy='joined')
    category = db.relationship('Category', lazy='joined')

class Projection(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    date = db.Column(db.Date, nullable=False)
    direction = db.Column(db.String(10), nullable=False)  # 'in' o 'out'
    amount = db.Column(db.Float, nullable=False, default=0.0)
    description = db.Column(db.String(500), default="")
    scope = db.Column(db.String(20), default="personal")
    account_id = db.Column(db.Integer, db.ForeignKey('account.id'))
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'))

    account = db.relationship('Account', lazy='joined')
    category = db.relationship('Category', lazy='joined')

def init_defaults():
    if not Account.query.first():
        db.session.add_all([Account(name='Banco'), Account(name='Efectivo'), Account(name='Tarjeta')])
    if not Category.query.first():
        db.session.add_all([Category(name='comida'), Category(name='servicios'), Category(name='impuestos'),
                            Category(name='transporte'), Category(name='otros')])
    db.session.commit()

with app.app_context():
    db.create_all()
    init_defaults()

# --- Helpers ---
def to_date(s):
    if isinstance(s, date):
        return s
    if not s:
        return date.today()
    s = str(s).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except Exception:
            pass
    # fallback dd-mm-yyyy like
    try:
        parts = s.replace('.', '-').replace('/', '-').split('-')
        if len(parts) == 3:
            d, m, y = parts
            if len(y) == 2:
                y = '20' + y
            return date(int(y), int(m), int(d))
    except Exception:
        pass
    return date.today()

def get_or_create(model, name):
    obj = model.query.filter(func.lower(model.name) == name.lower()).first()
    if not obj:
        obj = model(name=name)
        db.session.add(obj)
        db.session.commit()
    return obj

def month_bounds(ym):
    try:
        y, m = map(int, ym.split('-'))
        start = date(y, m, 1)
        end = date(y + (m==12), (m % 12) + 1, 1)
        return start, end
    except Exception:
        today = date.today()
        start = date(today.year, today.month, 1)
        end = date(today.year + (today.month==12), (today.month % 12) + 1, 1)
        return start, end


def month_start(d):
    return date(d.year, d.month, 1)


def add_months(d, months):
    base = d.year * 12 + (d.month - 1) + months
    year = base // 12
    month = base % 12 + 1
    return date(year, month, 1)


def month_label(d):
    return f"{d.year:04d}-{d.month:02d}"

# --- Dashboard ---
@app.route('/')
def dashboard():
    month = request.args.get('month')  # YYYY-MM
    start_str = request.args.get('start')
    end_str = request.args.get('end')

    if start_str and end_str:
        start = to_date(start_str)
        end = to_date(end_str) + timedelta(days=1)
        title_suffix = f"{start.isoformat()} a { (end - timedelta(days=1)).isoformat()}"
    else:
        if not month:
            month = f"{date.today().year:04d}-{date.today().month:02d}"
        start, end = month_bounds(month)
        title_suffix = month

    end_for_series = end - timedelta(days=1)
    end_for_series = month_start(end_for_series)
    actual_start_month = add_months(end_for_series, -11)
    actual_range_start = actual_start_month
    actual_range_end = add_months(end_for_series, 1)

    # Totales por scope
    totals = (
        db.session.query(
            Transaction.scope,
            func.sum(case((Transaction.direction == 'in', Transaction.amount), else_=0)).label('ingresos'),
            func.sum(case((Transaction.direction == 'out', Transaction.amount), else_=0)).label('gastos')
        )
        .filter(Transaction.date >= start, Transaction.date < end)
        .group_by(Transaction.scope)
        .all()
    )
    personal = {'ing': 0.0, 'gas': 0.0}
    negocio = {'ing': 0.0, 'gas': 0.0}
    for scope, ing, gas in totals:
        if scope == 'personal':
            personal['ing'] = float(ing or 0)
            personal['gas'] = float(gas or 0)
        elif scope == 'negocio':
            negocio['ing'] = float(ing or 0)
            negocio['gas'] = float(gas or 0)

    # Personal por categoría (gastos)
    cat_personal_expenses = (
        db.session.query(Category.name, func.sum(Transaction.amount))
        .join(Transaction.category)
        .filter(Transaction.scope == 'personal', Transaction.direction == 'out',
                Transaction.date >= start, Transaction.date < end)
        .group_by(Category.name).all()
    )
    personal_expense_labels = []
    personal_expense_values = []
    for name, total in cat_personal_expenses:
        personal_expense_labels.append(name)
        personal_expense_values.append(float(total or 0))

    # Negocio por categoría (gastos)
    cat_business_expenses = (
        db.session.query(Category.name, func.sum(Transaction.amount))
        .join(Transaction.category)
        .filter(Transaction.scope == 'negocio', Transaction.direction == 'out',
                Transaction.date >= start, Transaction.date < end)
        .group_by(Category.name).all()
    )
    business_expense_labels = []
    business_expense_values = []
    for name, total in cat_business_expenses:
        business_expense_labels.append(name)
        business_expense_values.append(float(total or 0))

    # Personal ingresos vs gastos por categoría
    cat_personal_mix = (
        db.session.query(
            Category.name,
            func.sum(case((Transaction.direction == 'in', Transaction.amount), else_=0)).label('ing'),
            func.sum(case((Transaction.direction == 'out', Transaction.amount), else_=0)).label('gas')
        )
        .join(Transaction.category)
        .filter(Transaction.scope == 'personal', Transaction.date >= start, Transaction.date < end)
        .group_by(Category.name).all()
    )
    personal_mix_labels = []
    personal_mix_in = []
    personal_mix_out = []
    for name, ing, gas in cat_personal_mix:
        personal_mix_labels.append(name)
        personal_mix_in.append(float(ing or 0))
        personal_mix_out.append(float(gas or 0))

    # Negocio ingresos vs gastos por categoría (para barras)
    cat_business = (
        db.session.query(
            Category.name,
            func.sum(case((Transaction.direction == 'in', Transaction.amount), else_=0)).label('ing'),
            func.sum(case((Transaction.direction == 'out', Transaction.amount), else_=0)).label('gas')
        )
        .join(Transaction.category)
        .filter(Transaction.scope == 'negocio', Transaction.date >= start, Transaction.date < end)
        .group_by(Category.name).all()
    )
    business_labels = []
    business_in = []
    business_out = []
    for name, ing, gas in cat_business:
        business_labels.append(name)
        business_in.append(float(ing or 0))
        business_out.append(float(gas or 0))

    # Últimos 12 meses (gastos personal / negocio)
    month_expr = func.strftime('%Y-%m', Transaction.date).label('month')
    filter_end_series = end if start_str and end_str else actual_range_end

    monthly_rows = (
        db.session.query(month_expr, Transaction.scope, Transaction.direction,
                         func.sum(Transaction.amount))
        .filter(Transaction.date>=actual_range_start, Transaction.date<filter_end_series)
        .group_by(month_expr, Transaction.scope, Transaction.direction)
        .all()
    )
    monthly_totals = {(m, s, d): float(total or 0) for m, s, d, total in monthly_rows}

    series_labels_actual = [month_label(add_months(actual_start_month, i)) for i in range(12)]
    personal_monthly_in = [monthly_totals.get((label, 'personal', 'in'), 0.0) for label in series_labels_actual]
    personal_monthly_out = [monthly_totals.get((label, 'personal', 'out'), 0.0) for label in series_labels_actual]
    negocio_monthly_in = [monthly_totals.get((label, 'negocio', 'in'), 0.0) for label in series_labels_actual]
    negocio_monthly_out = [monthly_totals.get((label, 'negocio', 'out'), 0.0) for label in series_labels_actual]

    series_personal = [ing - gas for ing, gas in zip(personal_monthly_in, personal_monthly_out)]
    series_negocio = [ing - gas for ing, gas in zip(negocio_monthly_in, negocio_monthly_out)]

    # Proyecciones futuras por mes
    future_start = add_months(end_for_series, 1)
    proj_month_expr = func.strftime('%Y-%m', Projection.date).label('month')
    projection_rows = (
        db.session.query(proj_month_expr, Projection.scope, Projection.direction,
                         func.sum(Projection.amount))
        .filter(Projection.date >= future_start)
        .group_by(proj_month_expr, Projection.scope, Projection.direction)
        .order_by(proj_month_expr)
        .all()
    )
    projection_totals = defaultdict(float)
    for month_val, scope_val, direction_val, total in projection_rows:
        projection_totals[(month_val, scope_val, direction_val)] += float(total or 0)
    future_labels = list(dict.fromkeys(row[0] for row in projection_rows))

    series_labels = series_labels_actual + future_labels
    personal_proj = [None] * len(series_labels_actual)
    negocio_proj = [None] * len(series_labels_actual)
    for label in future_labels:
        personal_out = projection_totals.get((label, 'personal', 'out'), 0.0)
        negocio_out = projection_totals.get((label, 'negocio', 'out'), 0.0)
        personal_in = projection_totals.get((label, 'personal', 'in'), 0.0)
        negocio_in = projection_totals.get((label, 'negocio', 'in'), 0.0)
        personal_proj.append(personal_in - personal_out)
        negocio_proj.append(negocio_in - negocio_out)

    series_personal_extended = series_personal + [None] * len(future_labels)
    series_negocio_extended = series_negocio + [None] * len(future_labels)

    # Proyecciones del mes (simple sum)
    proj_in = db.session.query(func.sum(Projection.amount)).filter(
        Projection.direction=='in', Projection.date>=start, Projection.date<end).scalar() or 0
    proj_out = db.session.query(func.sum(Projection.amount)).filter(
        Projection.direction=='out', Projection.date>=start, Projection.date<end).scalar() or 0

    return render_template('dashboard.html',
        title_suffix=title_suffix,
        month=month or "",
        start_value=start_str or "",
        end_value=end_str or "",
        personal=personal, negocio=negocio,
        personal_expense_labels=personal_expense_labels,
        personal_expense_values=personal_expense_values,
        personal_mix_labels=personal_mix_labels,
        personal_mix_in=personal_mix_in,
        personal_mix_out=personal_mix_out,
        business_expense_labels=business_expense_labels,
        business_expense_values=business_expense_values,
        business_labels=business_labels,
        business_in=business_in,
        business_out=business_out,
        series_labels=series_labels,
        series_personal=series_personal_extended,
        series_negocio=series_negocio_extended,
        series_personal_proj=personal_proj,
        series_negocio_proj=negocio_proj,
        proj_in=float(proj_in or 0), proj_out=float(proj_out or 0)
    )

# --- Movimientos ---
@app.route('/transactions')
def transactions():
    accounts = Account.query.all()
    categories = Category.query.all()

    q = Transaction.query
    scope = request.args.get('scope')
    account = request.args.get('account')
    category = request.args.get('category')
    start_str = request.args.get('start')
    end_str = request.args.get('end')

    if scope and scope != 'todos':
        q = q.filter(Transaction.scope == scope)
    if account and account.isdigit():
        q = q.filter(Transaction.account_id == int(account))
    if category and category.isdigit():
        q = q.filter(Transaction.category_id == int(category))
    if start_str and end_str:
        s = to_date(start_str)
        e = to_date(end_str) + timedelta(days=1)
        q = q.filter(Transaction.date >= s, Transaction.date < e)

    items = q.order_by(Transaction.date.desc(), Transaction.id.desc()).all()
    return render_template('transactions.html', items=items, accounts=accounts, categories=categories)

@app.route('/transactions/new', methods=['POST'])
def transactions_new():
    t = Transaction(
        date = to_date(request.form.get('date')),
        direction = request.form.get('direction', 'out'),
        amount = abs(float(request.form.get('amount') or 0)),
        description = request.form.get('description', ''),
        scope = request.form.get('scope', 'personal'),
    )
    t.account = get_or_create(Account, request.form.get('account', 'Banco'))
    t.category = get_or_create(Category, request.form.get('category', 'otros'))
    file = request.files.get('attachment')
    if file and file.filename:
        filename = secure_filename(file.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(path)
        t.attachment = filename
    db.session.add(t)
    db.session.commit()
    flash('Movimiento creado', 'success')
    return redirect(url_for('transactions'))

@app.route('/transactions/<int:tid>/edit', methods=['POST'])
def transactions_edit(tid):
    t = Transaction.query.get_or_404(tid)
    t.date = to_date(request.form.get('date')) or t.date
    t.direction = request.form.get('direction', t.direction)
    t.amount = abs(float(request.form.get('amount') or t.amount))
    t.description = request.form.get('description', t.description)
    t.scope = request.form.get('scope', t.scope)
    t.account = get_or_create(Account, request.form.get('account', t.account.name if t.account else 'Banco'))
    t.category = get_or_create(Category, request.form.get('category', t.category.name if t.category else 'otros'))
    file = request.files.get('attachment')
    if file and file.filename:
        filename = secure_filename(file.filename)
        path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(path)
        t.attachment = filename
    db.session.commit()
    flash('Movimiento actualizado', 'success')
    return redirect(url_for('transactions'))

@app.route('/transactions/<int:tid>/delete', methods=['POST'])
def transactions_delete(tid):
    t = Transaction.query.get_or_404(tid)
    db.session.delete(t)
    db.session.commit()
    flash('Movimiento eliminado', 'success')
    return redirect(url_for('transactions'))

@app.route('/uploads/<path:fname>')
def serve_upload(fname):
    path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
    return send_file(path, as_attachment=False)

# --- Import CSV/Excel ---
def parse_table(file_storage):
    filename = (file_storage.filename or '').lower()
    raw = file_storage.read()
    if filename.endswith('.csv'):
        # try utf-8, fallback latin1
        try:
            text = raw.decode('utf-8')
        except Exception:
            text = raw.decode('latin-1', errors='ignore')
        sep = ';' if text.count(';') > text.count(',') else ','
        lines = [l for l in text.splitlines() if l.strip()]
        headers = [h.strip().strip('"') for h in lines[0].split(sep)]
        rows = []
        for line in lines[1:]:
            parts = [p.strip().strip('"') for p in line.split(sep)]
            while len(parts) < len(headers):
                parts.append("")
            rows.append(dict(zip(headers, parts)))
        return headers, rows
    else:
        try:
            import pandas as pd
            df = pd.read_excel(BytesIO(raw))
            df.columns = [str(c).strip() for c in df.columns]
            rows = df.fillna("").to_dict(orient='records')
            return list(df.columns), rows
        except Exception as e:
            raise RuntimeError("Para Excel necesitás pandas+openpyxl. Subí CSV o instalá dependencias.") from e

@app.route('/import_csv', methods=['GET', 'POST'])
def import_csv():
    if request.method == 'GET':
        accounts = Account.query.all()
        return render_template('import_csv.html', accounts=accounts)
    file = request.files.get('file')
    scope_default = request.form.get('scope', 'personal')
    type_default = request.form.get('type', 'Gasto')  # Gasto/Ingreso
    account_default = request.form.get('account', 'Banco')

    if not file or not file.filename:
        flash('Seleccioná un archivo CSV o Excel', 'danger')
        return redirect(url_for('import_csv'))
    try:
        headers, rows = parse_table(file)
    except Exception as e:
        flash(f'Error leyendo archivo: {e}', 'danger')
        return redirect(url_for('import_csv'))

    alias = {
        'date': ['date', 'fecha', 'dia', 'día'],
        'description': ['description', 'descripcion', 'descripción', 'detalle', 'concepto'],
        'amount': ['amount', 'monto', 'importe', 'valor'],
        'account': ['account', 'cuenta'],
        'category': ['category', 'categoria', 'categoría'],
        'scope': ['scope', 'alcance', 'ámbito', 'ambito'],
        'direction': ['direction', 'tipo', 'movimiento']
    }
    def find_col(key):
        for a in alias[key]:
            for h in headers:
                if str(h).strip().lower() == a:
                    return h
        return None

    col_date = find_col('date')
    col_desc = find_col('description')
    col_amount = find_col('amount')
    col_account = find_col('account')
    col_category = find_col('category')
    col_scope = find_col('scope')
    col_dir = find_col('direction')
    if not (col_date and col_desc and col_amount):
        flash('Faltan columnas obligatorias: date, description, amount', 'danger')
        return redirect(url_for('import_csv'))

    inserted = 0
    for r in rows:
        try:
            dt = to_date(r.get(col_date, ''))
            desc = (r.get(col_desc, '') or '').strip().replace('\r', ' ').replace('\n', ' ')
            amt_raw = str(r.get(col_amount, '')).replace(' ', '')
            if ',' in amt_raw and '.' in amt_raw:
                amt_raw = amt_raw.replace('.', '').replace(',', '.')
            else:
                amt_raw = amt_raw.replace(',', '.')
            amount = float(amt_raw)
            scope = (r.get(col_scope) or scope_default).strip().lower()
            direction_default = 'out' if type_default.lower().startswith('g') or amount < 0 else 'in'
            direction = (r.get(col_dir) or direction_default).strip().lower()
            account_name = (r.get(col_account) or account_default).strip() or 'Banco'
            category_name = (r.get(col_category) or '').strip() or 'otros'
            amount = abs(amount)
            t = Transaction(date=dt, description=desc, amount=amount, scope=scope,
                            direction=direction,
                            account=get_or_create(Account, account_name),
                            category=get_or_create(Category, category_name))
            db.session.add(t)
            inserted += 1
        except Exception:
            continue
    db.session.commit()
    flash(f'Importación completada. Movimientos insertados: {inserted}', 'success')
    return redirect(url_for('transactions'))

# --- Export ---
@app.route('/export_csv')
def export_csv():
    out = StringIO()
    out.write('date,description,amount,account,category,scope,direction,attachment\n')
    for t in Transaction.query.order_by(Transaction.date, Transaction.id).all():
        acc = t.account.name if t.account else ''
        cat = t.category.name if t.category else ''
        row = [t.date.strftime('%Y-%m-%d'), t.description.replace(',', ' '),
               f"{t.amount:.2f}", acc, cat, t.scope, t.direction, (t.attachment or '')]
        out.write(','.join(row) + '\n')
    out.seek(0)
    return send_file(BytesIO(out.getvalue().encode('utf-8')),
                     as_attachment=True, download_name='fintrack_export.csv',
                     mimetype='text/csv')

# --- Projections ---
@app.route('/projections', methods=['GET', 'POST'])
def projections_list():
    if request.method == 'POST':
        p = Projection(
            date=to_date(request.form.get('date')),
            direction=request.form.get('direction', 'out'),
            amount=abs(float(request.form.get('amount') or 0)),
            description=request.form.get('description', ''),
            scope=request.form.get('scope', 'personal')
        )
        p.account = get_or_create(Account, request.form.get('account', 'Banco'))
        p.category = get_or_create(Category, request.form.get('category', 'otros'))
        db.session.add(p)
        db.session.commit()
        flash('Proyección agregada', 'success')
        return redirect(url_for('projections_list'))
    accounts = Account.query.all()
    categories = Category.query.all()
    items = Projection.query.order_by(Projection.date.desc(), Projection.id.desc()).all()
    return render_template('projections.html', items=items, accounts=accounts, categories=categories)

@app.route('/projections/<int:pid>/delete', methods=['POST'])
def projections_delete(pid):
    p = Projection.query.get_or_404(pid)
    db.session.delete(p)
    db.session.commit()
    flash('Proyección eliminada', 'success')
    return redirect(url_for('projections_list'))

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
