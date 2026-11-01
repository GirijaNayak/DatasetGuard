import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';

type Category = {name: string; value: number};
type Missing = {name: string; missing_pct: number};

export function FindingPieChart({data,colors}:{data:Category[];colors:string[]}){
  return <div className="pie-chart-wrap"><ResponsiveContainer width="100%" height="100%"><PieChart><Pie data={data} dataKey="value" nameKey="name" innerRadius={55} outerRadius={80} paddingAngle={3}>{data.map((_,i)=><Cell key={i} fill={colors[i%colors.length]}/>)}</Pie><Tooltip/></PieChart></ResponsiveContainer></div>;
}

export function MissingnessChart({data}:{data:Missing[]}){
  return <ResponsiveContainer width="100%" height="100%"><BarChart data={data} layout="vertical" margin={{left:8,right:15,top:4,bottom:4}}><CartesianGrid strokeDasharray="3 3" horizontal={false}/><XAxis type="number" tickFormatter={(v)=>`${v}%`} domain={[0,100]} tick={{fontSize:10,fill:'#8a92a2'}} axisLine={false} tickLine={false}/><YAxis type="category" dataKey="name" width={105} tick={{fontSize:11,fill:'#596174'}} axisLine={false} tickLine={false}/><Tooltip formatter={(v:any)=>[`${v}%`,'Missing']}/><Bar dataKey="missing_pct" fill="#e9a34f" radius={[0,5,5,0]} barSize={13}/></BarChart></ResponsiveContainer>;
}
