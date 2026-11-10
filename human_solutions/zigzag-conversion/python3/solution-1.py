def convert(s, numRows):
    if numRows == 1:
        return s
    
    row_map = {row: "" for row in range(1, numRows + 1)}
    row, up = 1, True
    
    for letter in s:
        row_map[row] += letter
        
        if row == 1:
            up = True
        elif row == numRows:
            up = False
        
        if up and row < numRows:
            row += 1
        elif not up and row > 1:
            row -= 1
    
    converted = ""
    for row in range(1, numRows + 1):
        converted += row_map[row]
    
    return converted