int getDayOfWeek(int day, int month, int year) {
    // Define days of the week
    String[] daysOfWeek = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"};

    // Calculate the total days from a reference date (1971)
    int sum = 0;
    for (int i = 1971; i < year; i++) {
        sum += isLeapYear(i) ? 366 : 365;
    }

    int[] daysInMonth = {31, isLeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};

    for (int i = 0; i < month - 1; i++) {
        sum += daysInMonth[i];
    }
    sum += day;

    // Calculate the day of the week
    int dayOfWeekIndex = sum % 7;

    return dayOfWeekIndex;
}