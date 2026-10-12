def zigzag_conversion(input_string, num_rows):
    hm = {}  # Hashmap to store characters based on row number
    increasing = True  # Boolean to track row direction
    current_row = 1  # Start with row 1
    index = 0  # Index to track current character in input string

    while index < len(input_string):
        current_string = hm.get(current_row, '')  # Get current row's string from hashmap
        current_string += input_string[index]  # Add current character to the row
        hm[current_row] = current_string  # Update the row in the hashmap
        index += 1  # Move to the next character

        if increasing:
            current_row += 1  # Move to the next row if increasing
            if current_row == num_rows:
                increasing = False  # Change direction if reached the last row
        else:
            current_row -= 1  # Move to the previous row if decreasing
            if current_row == 1:
                increasing = True  # Change direction if reached the first row

    result = ''
    for row in hm.keys():
        result += hm[row]  # Concatenate characters row by row

    return result

# Test the function
input_string = "PAYPALISHIRING"
num_rows = 3
print(zigzag_conversion(input_string, num_rows))  # Output: "PAHNAPLSIIGYIR"