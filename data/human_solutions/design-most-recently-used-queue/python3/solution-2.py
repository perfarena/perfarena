def find_largest_odd_number(n):
    odd_numbers = ['1', '3', '5', '7', '9']
    
    while n and n[-1] not in odd_numbers:
        n = n[:-1]
    
    return n

# Test the function
input_str = "52"
result = find_largest_odd_number(input_str)
print(result)