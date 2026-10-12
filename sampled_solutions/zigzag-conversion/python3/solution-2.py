def convert_to_zigzag(s, num_rows):
    if num_rows == 1:
        return s

    rows = ['' for _ in range(num_rows)]
    current_row, increment = 0, 1

    for letter in s:
        rows[current_row] += letter
        if current_row == num_rows - 1:
            increment = -1
        elif current_row == 0:
            increment = 1
        current_row += increment

    return ''.join(rows)

# Test the function
input_string = "PAYPALISHIRING"
num_rows = 3
result = convert_to_zigzag(input_string, num_rows)
print(result)