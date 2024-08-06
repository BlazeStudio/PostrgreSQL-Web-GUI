import re
from functools import wraps
from markupsafe import Markup
import psycopg2
from flask import (Flask, render_template, request, abort, flash, redirect, url_for, jsonify)
from pygments import formatters, highlight, lexers
def syntax_highlight(data):
    if not data:
        return ''
    lexer = lexers.get_lexer_by_name('sql')
    formatter = formatters.HtmlFormatter(linenos=False)
    return highlight(data, lexer, formatter)


DEBUG = True
MAX_RESULT_SIZE = 50
ROWS_PER_PAGE = 40

app = Flask(__name__)
app.config.from_object(__name__)

dataset = None

class PostgresTools():

    def __init__(self, dbname, user, password, host='localhost', port=5432):
        self.dbname = dbname
        self.user = user
        self.password = password
        self.host = host
        self.port = port
        self.db = psycopg2.connect(dbname=dbname, user=user, password=password, host=host, port=port)
        self.cursor = self.db.cursor()


    @property
    def filename(self):
        return self.dbname

    @property
    def location(self):
        return f"PostgreSQL://{self.user}@{self.host}:{self.port}/{self.dbname}"

    @property
    def size(self):
        self.cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database()))")
        return self.cursor.fetchone()[0]

    @property
    def tables(self):
        self.cursor.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name;"
        )
        result = self.cursor.fetchall()
        if result is not None:
            return set([row[0] for row in result])
        else:
            return set()

    def get_table(self, table):
        try:
            self.cursor.execute('SELECT * FROM %s;' % table)
            return self.cursor.fetchall()
        except Exception as e:
            print(f"Error updating the cell: {e}")
            return None

    def table_sql(self, table):
        try:
            self.cursor.execute(
                "SELECT column_name, data_type, is_nullable FROM information_schema.columns WHERE table_name = %s;",
                (table,)
            )
            columns_info = self.cursor.fetchall()

            create_table_script = f"CREATE TABLE IF NOT EXISTS {table} (\n"
            for column_info in columns_info:
                column_name, data_type, is_nullable = column_info
                create_table_script += f"    {column_name} {data_type}"
                if is_nullable == 'NO':
                    create_table_script += " NOT NULL"
                create_table_script += ",\n"
            create_table_script = create_table_script.rstrip(",\n") + "\n)"

            # Fetch Primary Key constraint
            self.cursor.execute(
                "SELECT kcu.column_name FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_name = kcu.constraint_name "
                "WHERE tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY';",
                (table,)
            )
            primary_key_columns = [row[0] for row in self.cursor.fetchall()]

            if primary_key_columns:
                create_table_script += f",\nPRIMARY KEY ({', '.join(primary_key_columns)})"

            # Fetch Foreign Key constraints
            self.cursor.execute(
                "SELECT DISTINCT ccu.table_name AS foreign_table, ccu.column_name AS foreign_column "
                "FROM information_schema.table_constraints AS tc "
                "JOIN information_schema.key_column_usage AS kcu "
                "ON tc.constraint_name = kcu.constraint_name "
                "JOIN information_schema.constraint_column_usage AS ccu "
                "ON ccu.constraint_name = tc.constraint_name "
                "WHERE constraint_type = 'FOREIGN KEY' AND tc.table_name = %s;",
                (table,)
            )
            foreign_keys = self.cursor.fetchall()

            for foreign_key in foreign_keys:
                foreign_table, foreign_column = foreign_key
                create_table_script += f",\nFOREIGN KEY ({foreign_column}) REFERENCES {foreign_table} ({foreign_column})"

            create_table_script += "\nTABLESPACE pg_default;\n"
            create_table_script += f"\nALTER TABLE IF EXISTS {table}\n    OWNER to postgres;\n"

            return create_table_script
        except Exception as e:
            print(f"Error getting SQL query for table: {e}")
            return None

    def update_cell(self, sql):
        try:
            self.cursor.execute(sql)
            self.db.commit()
        except Exception as e:
            print(f"Error updating the cell: {e}")

    def get_table_info(self, table):
        try:
            self.cursor.execute(
                "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_name = %s;",
                (table,)
            )
            info = self.cursor.fetchall()

            # Retrieve primary key column names
            self.cursor.execute(
                "SELECT column_name FROM information_schema.key_column_usage WHERE table_name = %s AND constraint_name = (SELECT constraint_name FROM information_schema.table_constraints WHERE table_name = %s AND constraint_type = 'PRIMARY KEY');",
                (table, table)
            )
            primary_key_columns = [row[0] for row in self.cursor.fetchall()]

            updated_info = []
            for col_info in info:
                updated_col_info = col_info + (('YES',) if col_info[0] in primary_key_columns else ('NO',))
                updated_info.append(updated_col_info)

            return updated_info
        except Exception as e:
            print(f"Error getting table information: {e}")
            return None

    def get_foreign_keys(self, table):
        return self.cursor.execute("PRAGMA foreign_key_list('%s')" % table).fetchall()

    def get_indexes(self, table):
        return self.cursor.execute("PRAGMA index_list('%s')" % table).fetchall()

    def paginate(self, table, page, paginate_by=20, order=None):
        if page > 0:
            page -= 1
        if order:
            sql = 'SELECT * FROM %s ORDER BY %s LIMIT %%s OFFSET %%s;' % (table, order)
        else:
            sql = 'SELECT * FROM %s LIMIT %%s OFFSET %%s;' % table

        try:
            self.cursor.execute(sql, (paginate_by, page * paginate_by))
            table_page = self.cursor.fetchall()
            return table_page
        except Exception as e:
            print(f"Error when executing the paginate request: {e}")
            return None


    def delete_table(self, table):
        self.cursor.execute("DROP TABLE %s" % table)

    def copy_table(self, old_table, new_table):
        infos = self.get_table_info(old_table)
        old_columns = ','.join([row[1] for row in infos])
        if 'default' in old_columns:
            old_columns = old_columns.replace('default', '"default"')
        infos = self.get_table_info(new_table)
        new_columns = ','.join([row[1] for row in infos])
        sql = 'INSERT INTO %s(%s) SELECT %s FROM %s;' % (
            new_table, new_columns, old_columns, old_table)
        self.cursor.execute(sql)

    def delete_column(self, table, column):
        try:
            self.cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table,)
            )
            existing_columns = [row[0] for row in self.cursor.fetchall()]

            if column not in existing_columns:
                flash('The column "%s" does not exist in the table' % column, 'danger')
                return

            sql = f'ALTER TABLE {table} DROP COLUMN {column}'
            print(sql)
            self.cursor.execute(sql)

            flash('Column "%s" has been successfully deleted from the table' % column, 'success')
        except Exception as e:
            print(f"Error deleting column: {e}")
            flash('An error occurred when deleting a column: %s' % e, 'danger')
        finally:
            self.db.commit()

    def add_column(self, table, column, column_type2, not_null, atr):
        try:
            self.cursor.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table,)
            )
            existing_columns = [row[0] for row in self.cursor.fetchall()]

            if column in existing_columns:
                return False
            self.cursor.execute('ALTER TABLE %s ADD COLUMN %s %s %s' % (table, column, column_type2, not_null))

            self.db.commit()

            return True
        except Exception as e:
            print(f"Error adding column: {e}")
            return False

    def add_row(self, table, values):
        try:
            self.cursor.execute(
                "SELECT column_name, is_nullable, data_type FROM information_schema.columns WHERE table_name = %s;",
                (table,)
            )
            columns_info = self.cursor.fetchall()

            column_names = [col_info[0] for col_info in columns_info]

            query = f"INSERT INTO {table} ("
            query += ", ".join(column_names)
            query += ") VALUES ("

            placeholders = []
            values_to_insert = []

            for column_name in column_names:
                if column_name in values:
                    placeholders.append("%s")
                    values_to_insert.append(values[column_name])
                elif column_name in column_names:
                    placeholders.append("%s")
                    values_to_insert.append(1)
                else:
                    placeholders.append("DEFAULT")

            query += ", ".join(placeholders)
            query += ")"

            self.cursor.execute(query, values_to_insert)
            self.db.commit()
        except Exception as e:
            flash(f'{e}', 'danger')

def require_database(fn):
    @wraps(fn)
    def inner(table, *args, **kwargs):
        if not database:
            return redirect(url_for('index'))
        if table not in dataset.tables:
            abort(404)
        return fn(table, *args, **kwargs)
    return inner



@app.route('/', methods=('GET', 'POST'))
def index():
    global database
    database = "<FileStorage: 'example.db' ('application/octet-stream')>"
    if not dataset:
        if request.method == 'POST':
            port = request.form.get('port')
            if int(port) < 0:
                flash(f'Error logging in. Invalid port value', 'danger')
                return render_template('index.html')
            dbname = request.form.get('dbname')
            user = request.form.get('username')
            password = request.form.get('password')
            host = request.form.get('host')
            try:
                join(dbname,user,password,host,port)
            except Exception:
                flash(f'Error logging in. Check that the data is correct.', 'danger')
    return render_template('index.html')


@app.route('/<table>', methods=('GET', 'POST'))
@require_database
def table_info(table):
    return render_template(
        'table_structure.html',
        columns=dataset.get_table(table),
        infos=dataset.get_table_info(table),
        table=table,
        # indexes=dataset.get_indexes(table),
        # foreign_keys=dataset.get_foreign_keys(table),
        table_sql=dataset.table_sql(table))


@app.route('/<table>/rename-column', methods=['GET', 'POST'])
@require_database
def rename_column(table):
    rename = request.args.get('rename')
    infos = dataset.get_table_info(table)
    column_names = [row[0] for row in infos]
    if request.method == 'POST':
        new_name = request.form.get('rename_to', '')
        rename = request.form.get('rename', '')
        if new_name and new_name not in column_names:
            try:
                dataset.cursor.execute(f'ALTER TABLE {table} RENAME COLUMN {rename} TO {new_name}')
                dataset.db.commit()
                flash(f'The column "{rename}" has been successfully renamed to "{new_name}"!', 'success')
            except Exception as e:
                flash(f'Error when renaming a column: {e}', 'danger')
        else:
            flash('The column name must not be empty or match another one', 'danger')
        return redirect(url_for('rename_column', table=table))
    return render_template(
        'rename_column.html',
        infos=infos,
        table=table,
        rename=rename,
    )



@app.route('/<table>/delete-column/', methods=['GET', 'POST'])
@require_database
def delete_column(table):
    name = request.args.get('name')
    infos = dataset.get_table_info(table)
    if request.method == 'POST':
        name = request.form.get('name', '')
        if (name == None): flash('The column is not specified', 'danger')
        else:
            dataset.delete_column(table, name)
        return redirect(url_for('table_info', table=table))
    return render_template(
        'delete_column.html',
        infos=infos,
        table=table,
        name=name)



@app.route('/<table>/add-column/', methods=['GET', 'POST'])
@require_database
def add_column(table):
    column_mapping = ['VARCHAR', 'TEXT', 'INTEGER', 'REAL',
                      'BOOL', 'BLOB', 'DATETIME', 'DATE', 'TIME', 'DECIMAL']
    if request.method == 'POST':
        name = request.form.get('name', '')
        column_type = request.form.get('type', '')
        not_null = 'NOT NULL' if request.form.get('not_null') else ''
        unique = 'UNIQUE' if request.form.get('unique') else ''
        autoincrement = 'AUTOINCREMENT' if request.form.get('autoincrement') else ''
        atr = unique + not_null + autoincrement
        if name and column_type:
            success = dataset.add_column(table, name, column_type, not_null, atr)
            if success:
                flash('Column "%s" was successfully created' % name, 'success')
            else:
                if not_null == 'NOT NULL': flash('The table contains rows, it is impossible to add a column with the NOT NULL attribute', 'danger')
                else: flash('A column with the same name already exists', 'danger')
        else:
            flash('The name and type cannot be empty', 'danger')
        return redirect(url_for('add_column', table=table))
    return render_template('add_column.html', column_mapping=column_mapping, table=table)


@app.route('/<table>/<edit>/add-row/', methods=['GET', 'POST'])
@require_database
def add_row(table, edit):
    if request.method == 'POST':
        values = {}
        for column_info in dataset.get_table_info(table):
            column_name = column_info[0]
            values[column_name] = None if request.form.get(column_name) == '' else request.form.get(column_name)
        dataset.add_row(table, values)
    return redirect(url_for('table_content', table=table, edit=edit))


@app.route('/apply_changes', methods=['POST'])
def apply_changes():
    table = request.form.get('table_name')
    name = request.form.get('columnLabel').strip()
    row = int(request.form.get('rowLabel'))
    new_value = request.form.get('newValue')
    try:
        sql = f"UPDATE {table} SET {name} = '{new_value}' WHERE id = {row}"
        dataset.update_cell(sql)

        return jsonify({'message': 'The data has been successfully updated in the database.'})
    except Exception as e:
        return jsonify({'error': str(e)})

@app.route('/<table>/<edit>/delete-row/', methods=['POST'])
@require_database
def delete_row(table, edit):
    row_id = eval(request.form.get('row_id'))
    try:
        columns_info = dataset.get_table_info(table)
        sql = f"DELETE FROM {table} WHERE "
        new_mass = []
        for names in columns_info:
            new_mass.append(names[0])
        for i in range(len(new_mass)):
            if row_id[i] != None:
                sql += f"{new_mass[i]} = '{row_id[i]}' AND "
        sql = sql[:-5]
        dataset.cursor.execute(sql)
        dataset.db.commit()
        flash('The row was successfully deleted.', 'success')
    except Exception as e:
        flash(f'Error deleting a row: {e}', 'danger')
    return redirect(url_for('table_content', table=table, edit=edit))


@app.route('/<table>/<edit>/content', methods=['GET', 'POST'])
@require_database
def table_content(table, edit):
    columns_count = dataset.get_table(table)
    ordering = request.args.get('ordering')
    rows_per_page = app.config['ROWS_PER_PAGE']
    page = request.args.get('page', 1, type=int)
    if ordering:
        columns = dataset.paginate(
            table, page, paginate_by=rows_per_page, order=ordering)
    else:
        columns = dataset.paginate(
            table=table, page=page, paginate_by=rows_per_page)
    total_pages = (len(columns_count) // rows_per_page) + 1
    previous_page = page - 1
    next_page = page + 1 if page + \
        1 <= total_pages else 0
    return render_template(
        'table_content.html',
        columns=columns,
        ordering=ordering,
        page=page,
        total_pages=total_pages,
        previous_page=previous_page,
        next_page=next_page,
        columns_count=columns_count,
        infos=dataset.get_table_info(table),
        table=table,
        edit=edit
    )


@app.route('/<table>/query/', methods=['GET', 'POST'])
@require_database
def table_query(table):
    row_count, error, data, data_description = None, None, None, None
    cursor = dataset.db.cursor()

    if request.method == 'POST':
        sql = request.form.get('sql', '')
        try:
            cursor.execute(sql)
            dataset.db.commit()
            data = cursor.fetchall()[:app.config['MAX_RESULT_SIZE']]
            data_description = cursor.description
            row_count = len(data)
        except Exception as exc:
            error = str(exc)
            if error == "no results to fetch": error = "Success!"
    else:
        if request.args.get('sql'):
            sql = request.args.get('sql')
        else:
            sql = f'SELECT * FROM "{table}"'

    return render_template(
        'table_query.html',
        row_count=row_count,
        data=data,
        data_description=data_description,
        table=table,
        sql=sql,
        error=error,
        table_sql=dataset.table_sql(table)
    )



@app.route('/table_create/', methods=['POST'])
def table_create():
    table = request.form.get('table_name', '')
    if not table:
        flash('Enter the name of the table.', 'danger')
        return redirect(request.referrer)
    try:
        dataset.cursor.execute(f'CREATE TABLE {table}(id SERIAL PRIMARY KEY)')
        dataset.db.commit()
        return redirect(url_for('table_info', table=table))
    except Exception as e:
        flash(f'Error creating the table: {str(e)}', 'danger')
        return redirect(request.referrer)


@app.route('/<table>/delete', methods=['GET', 'POST'])
@require_database
def delete_table(table):
    if request.method == 'POST':
        try:
            dataset.cursor.execute('DROP TABLE %s' % table)
            dataset.db.commit()
        except Exception as exc:
            flash('Error deleting the table: %s' % exc, 'danger')
        else:
            flash('The table "%s" was successfully deleted.' % table, 'success')
            return redirect(url_for('index'))
    return render_template('delete_table.html', table=table)


@app.route('/close')
def close():
    global database
    global dataset
    dataset = None
    database = None
    return redirect(url_for('index'))


column_re = re.compile('(.+?)\((.+)\)', re.S)
column_split_re = re.compile(r'(?:[^,(]|\([^)]*\))+')


def _format_create_table(sql):
    create_table, column_list = column_re.search(sql).groups()
    columns = ['  %s' % column.strip()
               for column in column_split_re.findall(column_list)
               if column.strip()]
    return '%s (\n%s\n)' % (
        create_table,
        ',\n'.join(columns))


@app.template_filter()
def format_create_table(sql):
    try:
        return _format_create_table(sql)
    except:
        return sql


@app.template_filter('highlight')
def highlight_filter(data):
    return Markup(syntax_highlight(data))


@app.context_processor
def _general():
    return {
        'dataset': dataset,
    }

def join(dbname, user, password, host, port):
    global dataset
    dataset = PostgresTools(dbname, user, password, host, port)

# @app.before_request
# def _before_request():
#     global dataset
#     if database:
#         #Change values for your postgresql database(FOR TEST)
#         # dbname = 'Hotel'
#         # user = 'postgres'
#         # password = '12345'
#         # host = 'localhost'
#         # port = 5432
#         # dataset = PostgresTools(dbname, user, password, host, port)

def main():
    global database
    database = None
    app.run()

if __name__ == '__main__':
    main()
