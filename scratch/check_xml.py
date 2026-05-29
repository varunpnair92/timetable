import zipfile
import re

with zipfile.ZipFile('test.xlsx') as z:
    # Read sheet1.xml
    sheet1 = z.read('xl/worksheets/sheet1.xml').decode('utf-8')
    print("Is 'Test Single' in sheet1.xml?", 'Test Single' in sheet1)
    
    # Read sharedStrings.xml if it exists
    try:
        shared_strings = z.read('xl/sharedStrings.xml').decode('utf-8')
        print("Is 'Test Single' in sharedStrings.xml?", 'Test Single' in shared_strings)
        print("Is 'Test Multi' in sharedStrings.xml?", 'Test Multi' in shared_strings)
    except KeyError:
        print("No sharedStrings.xml found")
