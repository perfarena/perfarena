rows = len(matrix)
columns = len(matrix[0])
row_flag = False
column_flag = False

for row in range(rows):
    for column in range(columns):
        if matrix[row][column] == 0:
            if row == 0:
                row_flag = True
            if column == 0:
                column_flag = True
            matrix[row][0] = 0
            matrix[0][column] = 0

for row in range(1, rows):
    for column in range(1, columns):
        if matrix[row][0] == 0 or matrix[0][column] == 0:
            matrix[row][column] = 0

if row_flag:
    for column in range(columns):
        matrix[0][column] = 0

if column_flag:
    for row in range(rows):
        matrix[row][0] = 0