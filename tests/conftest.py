"""Keep collection-time imports away from the application's preview database."""
import os
import tempfile

os.environ['DB_PATH'] = os.path.join(tempfile.mkdtemp(prefix='sigi-suite-'), 'suite.db')
os.environ['SUPABASE_DATABASE_URL'] = ''
