-- workload/queries.sql - the 20-query workload for Module 4 measurements.

-- Q1: selective equality on Emp
SELECT Emp.name FROM Emp WHERE Emp.id = 42;

-- Q2: non-selective equality on a low-ndv column
SELECT Emp.name FROM Emp WHERE Emp.region = 'NA';

-- Q3: range on Emp.age
SELECT Emp.name FROM Emp WHERE Emp.age > 60;

-- Q4: conjunction, independent columns
SELECT Emp.name FROM Emp WHERE Emp.age > 40 AND Emp.salary > 100000;

-- Q5: two-table join with a filter above
SELECT Emp.name, Dept.title
  FROM Emp JOIN Dept ON Emp.did = Dept.did
 WHERE Emp.age > 50;

-- Q6: two-table join with filter pushed (same query, comma form)
SELECT Emp.name, Dept.title
  FROM Emp, Dept
 WHERE Emp.did = Dept.did AND Emp.age > 50;

-- Q7: join with non-trivial selectivity
SELECT Emp.name, Dept.budget
  FROM Emp JOIN Dept ON Emp.did = Dept.did
 WHERE Dept.budget > 200000;

-- Q8: join on a low-ndv column
SELECT Emp.name, Dept.title
  FROM Emp JOIN Dept ON Emp.did = Dept.did
 WHERE Emp.region = 'EU';

-- Q9: three-table join
SELECT Emp.name, Dept.title, Region.country
  FROM Emp
  JOIN Dept   ON Emp.did    = Dept.did
  JOIN Region ON Emp.region = Region.region
 WHERE Emp.age > 30;

-- Q10: three-table join, different order
SELECT Emp.name, Dept.city, Region.country
  FROM Emp
  JOIN Region ON Emp.region = Region.region
  JOIN Dept   ON Emp.did    = Dept.did
 WHERE Dept.budget > 150000;

-- Q11: three-table join with selective filter
SELECT Emp.name, Dept.title, Region.country
  FROM Emp
  JOIN Dept   ON Emp.did    = Dept.did
  JOIN Region ON Emp.region = Region.region
 WHERE Emp.salary > 150000 AND Dept.budget > 200000;

-- Q12: three-table join with aggregation
SELECT Dept.title, COUNT(*)
  FROM Emp JOIN Dept ON Emp.did = Dept.did
 WHERE Emp.age > 40
 GROUP BY Dept.title;

-- Q13: aggregation over Emp
SELECT Emp.region, COUNT(*), AVG(Emp.salary)
  FROM Emp
 GROUP BY Emp.region;

-- Q14: aggregation with HAVING
SELECT Emp.did, COUNT(*) AS n
  FROM Emp
 GROUP BY Emp.did
HAVING COUNT(*) > 200;

-- Q15: aggregation over a join
SELECT Dept.city, SUM(Emp.salary)
  FROM Emp JOIN Dept ON Emp.did = Dept.did
 GROUP BY Dept.city;

-- Q16: aggregation with a filter
SELECT Emp.did, AVG(Emp.age)
  FROM Emp
 WHERE Emp.salary > 100000
 GROUP BY Emp.did;

-- Q17: CORRELATED COLUMNS - mismatched regions
SELECT Emp.name
  FROM Emp
 WHERE Emp.did = 5 AND Emp.region = 'EU';

-- Q18: CORRELATED COLUMNS - matching regions
SELECT Emp.name
  FROM Emp
 WHERE Emp.did = 5 AND Emp.region = 'NA';

-- Q19: CORRELATED COLUMNS with a join
SELECT Emp.name, Region.country
  FROM Emp JOIN Region ON Emp.region = Region.region
 WHERE Emp.did = 5;

-- Q20: adversarial - filter on the correlated pair, then aggregate
SELECT Emp.region, COUNT(*)
  FROM Emp
 WHERE Emp.did = 5 OR Emp.did = 6 OR Emp.did = 7
 GROUP BY Emp.region;