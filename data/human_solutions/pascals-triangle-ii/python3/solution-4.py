def getRow(rowIndex):
    result = [1]
    
    for i in range(1, rowIndex + 1):
        temp = [1]
        for j in range(1, len(result)):
            temp.append(result[j-1] + result[j])
        temp.append(1)
        result = temp
    
    return result