def calculate_money(n):
    # Calculate the number of complete weeks
    weeks = n // 7
    
    # Calculate the remaining days
    remaining_days = n % 7
    
    # Calculate the contribution from complete weeks
    complete_weeks_contribution = weeks * (weeks - 1) // 2 * 7
    
    # Calculate the contribution from remaining days
    remaining_contribution = remaining_days * (remaining_days + 1) // 2
    
    # Calculate the total amount
    total_amount = complete_weeks_contribution + remaining_contribution
    
    return total_amount

# Test the function with a sample input
n = 20
print(calculate_money(n))  # Output should be 96