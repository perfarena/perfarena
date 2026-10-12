# Format the day with leading zero if less than 10
day_str = str(day).zfill(2)

# Format the month with leading zero if less than 10
month_str = str(month).zfill(2)

# Create the formatted date string
date_str = f"{day_str}/{month_str}/{year}"