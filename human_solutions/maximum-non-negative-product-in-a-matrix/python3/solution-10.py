def calculate_total_amount(N):
    # Calculate the number of complete weeks
    K = N // 7
    
    # Calculate the sum for complete weeks using AP formula
    sum_complete_weeks = K // 2 * (28 + (28 + 7*(K-1)))
    
    # Calculate the sum for remaining days
    remaining_days = N % 7
    total_sum = sum_complete_weeks
    
    # Iterate through remaining days and add corresponding amounts
    for day in range(remaining_days):
        total_sum += 28 + K + day
    
    return total_sum

# Test the function with a sample input
N = 25
result = calculate_total_amount(N)
print("Total amount saved after", N, "days is:", result)