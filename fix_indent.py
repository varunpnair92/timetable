import sys
with open('fisat/views.py', 'r') as f:
    lines = f.readlines()

def fix_indent(start_line, end_line):
    for i in range(start_line - 1, end_line):
        if lines[i].startswith('                                '):
            lines[i] = '    ' + lines[i]

# Fix Chunk 3 (2800 to 2811)
fix_indent(2800, 2811)
# Fix Chunk 4 (2855 to 2866)
fix_indent(2855, 2866)

with open('fisat/views.py', 'w') as f:
    f.writelines(lines)
