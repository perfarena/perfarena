def calculate_money(days):
    total = 0
    week = 1

    while days > 0:
        for day in range(min(days, 7)):
            total += week + day
            days -= 1

        week += 1

    return total

# Example usage
days_to_save = 20
total_amount = calculate_money(days_to_save)
print(total_amount)