def generate_pascals_triangle_row(rowIndex):
    row = [1]
    for x in range(1, rowIndex + 1):
        row.append(row[x - 1] * (rowIndex - x + 1) // x)
    return row

# Test the function with the given rowIndex
rowIndex = 3
result = generate_pascals_triangle_row(rowIndex)
print(result)