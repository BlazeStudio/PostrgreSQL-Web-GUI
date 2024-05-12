import os
import re
import sqlite3
import datetime
from functools import wraps
from markupsafe import Markup
import psycopg2

try:
    from flask import (Flask, render_template, request, abort, session,
                       flash, redirect, url_for, make_response, send_file, send_from_directory, jsonify)
except ImportError:
    raise RuntimeError('Unable to import flask module. Install by running '
                       'pip install flask')
try:
    from pygments import formatters, highlight, lexers
except ImportError:
    import warnings
    warnings.warn('pygments library not found.', ImportWarning)
    syntax_highlight = lambda data: '<pre>%s</pre>' % data
else:
    def syntax_highlight(data):
        if not data:
            return ''
        lexer = lexers.get_lexer_by_name('sql')
        formatter = formatters.HtmlFormatter(linenos=False)
        return highlight(data, lexer, formatter)


# CUR_DIR = os.path.realpath(os.path.dirname(__file__))
DEBUG = True
SECRET_KEY = 'sqlite-database-browser-0.1.0'
MAX_RESULT_SIZE = 50
ROWS_PER_PAGE = 20
OUT_FOLDER = 'export_file'

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

    # @property
    # def created(self):
    #     self.cursor.execute("SELECT pg_stat_file('base/{0}/PG_VERSION')".format(self.dbname))
    #     pg_version_mtime = self.cursor.fetchone()[0]
    #     return datetime.datetime.fromtimestamp(pg_version_mtime)

    # @property
    # def modified(self):
    #     # Для PostgreSQL нет прямого способа получить время последнего изменения базы данных.
    #     # Мы можем использовать время последнего изменения файлов базы данных.
    #     # Например, мы можем использовать время последнего изменения файла PG_VERSION.
    #     self.cursor.execute("SELECT pg_stat_file('base/{0}/PG_VERSION')".format(self.dbname))
    #     pg_version_mtime = self.cursor.fetchone()[0]
    #     return datetime.datetime.fromtimestamp(pg_version_mtime)

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
            print(f"Ошибка при выполнении запроса: {e}")
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
            print(f"Ошибка при обновлении ячейки: {e}")

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

            # Add flag indicating whether each column is part of the primary key
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
            print(f"Ошибка при выполнении запроса paginate: {e}")
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
                flash('Столбец "%s" не существует в таблице' % column, 'danger')
                return

            sql = f'ALTER TABLE {table} DROP COLUMN {column}'
            print(sql)
            self.cursor.execute(sql)

            flash('Столбец "%s" успешно удален из таблицы' % column, 'success')
        except Exception as e:
            print(f"Error deleting column: {e}")
            flash('Произошла ошибка при удалении столбца: %s' % e, 'danger')
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

    # def add_row(self, table, values):
    #     #     try:
    #     #         self.cursor.execute(
    #     #             "SELECT column_name, is_nullable, data_type FROM information_schema.columns WHERE table_name = %s;",
    #     #             (table,)
    #     #         )
    #     #         columns_info = self.cursor.fetchall()
    #     #
    #     #         column_names = [col_info[0] for col_info in columns_info]
    #     #         non_nullable_columns = [col_info[0] for col_info in columns_info if col_info[1] == 'NO']
    #     #
    #     #         column_names_without_id = [col_name for col_name in column_names if col_name != 'id']
    #     #
    #     #         query = f"INSERT INTO {table} ("
    #     #         query += ", ".join(column_names_without_id)
    #     #         query += ") VALUES ("
    #     #
    #     #         placeholders = []
    #     #         values_to_insert = []
    #     #
    #     #         for column_name in column_names_without_id:
    #     #             if column_name in values:
    #     #                 placeholders.append("%s")
    #     #                 values_to_insert.append(values[column_name])
    #     #             elif column_name in non_nullable_columns:
    #     #                 placeholders.append("%s")
    #     #                 values_to_insert.append(1)
    #     #             else:
    #     #                 placeholders.append("DEFAULT")
    #     #
    #     #         query += ", ".join(placeholders)
    #     #         query += ")"
    #     #
    #     #         print(query)
    #     #
    #     #         self.cursor.execute(query, values_to_insert)
    #     #         self.db.commit()
    #     #     except Exception as e:
    #     #         print(f"Ошибка при добавлении строки: {e}")

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

            print(query)

            self.cursor.execute(query, values_to_insert)
            self.db.commit()
        except Exception as e:
            print(f"Ошибка при добавлении строки: {e}")

    def rename_cloumn(self, table, rename, rename_to):
        sql = self.cursor.execute('SELECT sql FROM sqlite_master WHERE tbl_name = ? AND type = ?',
                                  [table, 'table']).fetchone()[0]
        self.cursor.execute(
            "ALTER TABLE %s RENAME TO old_%s" % (table, table))
        r = '\\b' + rename + '\\b'
        sql = re.sub(r, rename_to, sql)
        self.cursor.execute(sql)
        self.copy_table("old_%s" % table, table)
        self.delete_table("old_%s" % table)

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
                flash(f'Ошибка при входе. Неверное значение порта', 'danger')
                return render_template('index.html')
            dbname = request.form.get('dbname')
            user = request.form.get('username')
            password = request.form.get('password')
            host = request.form.get('host')
            try:
                join(dbname,user,password,host,port)
            except Exception:
                flash(f'Ошибка при входе. Проверьте правильность данных.', 'danger')
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
    column_names = [row[1] for row in infos]
    if request.method == 'POST':
        new_name = request.form.get('rename_to', '')
        rename = request.form.get('rename', '')
        if new_name and new_name not in column_names:
            dataset.rename_cloumn(table, rename, new_name)
            flash('Столбец "%s" успешно переименован!' % rename, 'success')
        else:
            flash('Название столбца не должно быть пустым или совпадать с другим', 'danger')
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
        if (name == None): flash('Столбец не указан', 'danger')
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
                flash('Столбец "%s" был успешно создан' % name, 'success')
            else:
                if not_null == 'NOT NULL': flash('В таблице содержатся строчки, невозможно добавить столбец с атрибутом NOT NULL', 'danger')
                else: flash('Столбец с таким именем уже существует', 'danger')
        else:
            flash('Имя и тип не могут быть пустыми', 'danger')
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
        print(values)
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

        return jsonify({'message': 'Данные успешно обновлены в базе данных.'})
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
        flash('Строка успешно удалена.', 'success')
    except Exception as e:
        flash(f'Ошибка при удалении строки: {e}', 'danger')
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
            if error == "no results to fetch": error = "Успешно!"
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
        flash('Введите имя таблицы.', 'danger')
        return redirect(request.referrer)
    try:
        dataset.cursor.execute(f'CREATE TABLE {table}(id SERIAL PRIMARY KEY)')
        dataset.db.commit()
        return redirect(url_for('table_info', table=table))
    except Exception as e:
        flash(f'Ошибка при создании таблицы: {str(e)}', 'danger')
        return redirect(request.referrer)


@app.route('/<table>/delete', methods=['GET', 'POST'])
@require_database
def delete_table(table):
    if request.method == 'POST':
        try:
            dataset.cursor.execute('DROP TABLE %s' % table)
            dataset.db.commit()
        except Exception as exc:
            flash('Ошибка при удалении таблицы: %s' % exc, 'danger')
        else:
            flash('Таблица "%s" была успешно удалена.' % table, 'success')
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

@app.before_request
def _before_request():
    global dataset
    if database:
        # Параметры для подключения к PostgreSQL
        dbname = 'postgres'
        user = 'postgres'
        password = '12345'
        host = 'localhost'  # Или другой хост, на котором находится PostgreSQL
        port = 5432  # Порт PostgreSQL
        dataset = PostgresTools(dbname, user, password, host, port)

def main():
    global database
    database = None
    app.run()

if __name__ == '__main__':
    main()
