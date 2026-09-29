import re

def fix_duplicates(filepath, prefix):
    with open(filepath, "r") as f:
        content = f.read()

    # Remove the bad keys injected by my previous script
    content = content.replace('key="_v1_pcr_full", ', '')
    content = content.replace('key="_v1_pcr_near", ', '')
    content = content.replace('key="_v1_pcr_bands", ', '')
    
    content = content.replace('key="_v2_pcr_full", ', '')
    content = content.replace('key="_v2_pcr_near", ', '')
    content = content.replace('key="_v2_pcr_bands", ', '')

    # Prefix the existing keys
    # key="pcr_intervals" -> key="{prefix}_pcr_intervals"
    content = content.replace('key="pcr_intervals"', f'key="{prefix}_pcr_intervals"')
    content = content.replace('key="pcr_show_full"', f'key="{prefix}_pcr_show_full"')
    content = content.replace('key="pcr_show_near"', f'key="{prefix}_pcr_show_near"')
    content = content.replace('key="pcr_bands"', f'key="{prefix}_pcr_bands"')

    with open(filepath, "w") as f:
        f.write(content)

fix_duplicates("app/dashboard_v1.py", "_v1")
fix_duplicates("app/dashboard_v2.py", "_v2")
