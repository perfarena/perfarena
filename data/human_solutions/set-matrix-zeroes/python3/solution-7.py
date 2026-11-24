rows_to_zero = set()
columns_to_zero = set()

for i in range(len(matrix)):
    for j in range(len(matrix[0])):
        if matrix[i][j] == 0:
            rows_to_zero.add(i)
            columns_to_zero.add(j)

for i in range(len(matrix)):
    for j in range(len(matrix[0])):
        if i in rows_to_zero or j in columns_to_zero:
            matrix[i][j] = 0