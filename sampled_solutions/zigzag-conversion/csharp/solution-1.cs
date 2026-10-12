int numRows = 3;
string[] rows = new string[numRows];
int row = 0;
bool goingDown = true;

foreach (char c in "PayPal is hiring")
{
    rows[row] += c;

    if (goingDown)
    {
        row++;
        if (row == numRows - 1)
        {
            goingDown = false;
        }
    }
    else
    {
        row--;
        if (row == 0)
        {
            goingDown = true;
        }
    }
}

string output = "";
foreach (string r in rows)
{
    output += r;
}

return output;