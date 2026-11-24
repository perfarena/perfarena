# Create a datetime object from the formatted date
date_obj = datetime.datetime.strptime(date_str, '%d/%m/%Y')

# Get the day of the week as an integer (0 = Monday, 6 = Sunday)
weekday_int = date_obj.weekday()

# Get the day name from the calendar library
day_name = calendar.day_name[weekday_int]

# Print the day of the week
print(day_name)