def getRow(rowIndex):
    result = [0] * (rowIndex + 1)
    result[0] = 1
    
    for i in range(rowIndex):
        next_row = [0] * (rowIndex + 1)
        for j in range(len(result)):
            next_row[j] += result[j]
            next_row[j + 1] += result[j]
        result = next_row
    
    return result