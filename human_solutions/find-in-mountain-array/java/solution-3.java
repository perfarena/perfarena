String getDayOfWeekName(int day, int month, int year) {
    String[] daysOfWeek = {"Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"};
    int dayOfWeekIndex = getDayOfWeek(day, month, year);
    return daysOfWeek[dayOfWeekIndex];
}