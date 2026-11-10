def generate_pascals_triangle(row_index):
    previous = [1]  # Initialize the first row
    for i in range(1, row_index + 1):
        current = []  # Initialize the current row
        for j in range(i + 1):
            if j == 0 or j == i:
                current.append(1)
            else:
                current.append(previous[j-1] + previous[j])
        previous = current[:]  # Update the previous row with the current row
    return previous

# Test the function with a row index
row_index = 3
result = generate_pascals_triangle(row_index)
print(result)