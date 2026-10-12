def setZeroes(matrix):
    rows = len(matrix)
    cols = len(matrix[0])
    row_zero = False

    for i in range(rows):
        for j in range(cols):
            if matrix[i][j] == 0:
                if i == 0:
                    row_zero = True
                else:
                    matrix[i][0] = 0
                    matrix[0][j] = 0

    for i in range(1, rows):
        for j in range(1, cols):
            if matrix[i][0] == 0 or matrix[0][j] == 0:
                matrix[i][j] = 0

    if matrix[0][0] == 0:
        for j in range(cols):
            matrix[0][j] = 0

    if row_zero:
        for i in range(rows):
            matrix[i][0] = 0