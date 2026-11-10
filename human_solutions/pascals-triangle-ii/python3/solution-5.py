def generate(numRows):
    if numRows == 0:
        return []
    
    row = [1]
    result = [row]
    
    for _ in range(1, numRows):
        newRow = [l + r for l, r in zip([0] + row, row + [0])]
        result.append(newRow)
        row = newRow
    
    return result