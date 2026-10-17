import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["PD_TEST_BACKEND"] = "memory"   # tests never touch your real Supabase project
SAMPLES = os.path.join(ROOT, "sample_invoices")
MY_GSTIN = "06AAACD1234E1ZD"               # buyer GSTIN printed on the sample bills
