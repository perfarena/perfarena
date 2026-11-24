def calculate_total_saved(n):
    total = 0
    complete_weeks = n // 7
    remainder = n % 7
    
    # Calculate sum for complete weeks
    total += 28 * complete_weeks
    
    # Calculate sum for remaining days
    total += remainder * (remainder + 1) // 2
    
    # Calculate extra contribution for complete weeks greater than one
    total += complete_weeks * (complete_weeks - 1) // 2 * 7
    
    return total

# Test the function with a sample input
n = 20
result = calculate_total_saved(n)
print(result)