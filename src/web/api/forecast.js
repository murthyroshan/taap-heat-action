// Fetches the Open-Meteo forecast for the backend on Render. Render's shared outbound IP
// often hits Open-Meteo's free daily limit, so the backend asks through this function instead.
const UPSTREAM = 'https://api.open-meteo.com/v1/forecast';
const VARS = 'temperature_2m,relative_humidity_2m,wind_speed_10m,shortwave_radiation,direct_radiation,surface_pressure';
const COORDS = /^-?\d{1,3}(\.\d+)?(,-?\d{1,3}(\.\d+)?){0,19}$/; // at most 20 locations

module.exports = async (req, res) => {
  const { latitude, longitude } = req.query;
  if (!COORDS.test(latitude || '') || !COORDS.test(longitude || '')
      || latitude.split(',').length !== longitude.split(',').length) {
    return res.status(400).json({ error: true, reason: 'latitude and longitude lists required' });
  }
  const q = new URLSearchParams({ latitude, longitude, hourly: VARS, timezone: 'GMT', past_days: '1', forecast_days: '3' });
  try {
    const r = await fetch(`${UPSTREAM}?${q}`, { headers: { 'User-Agent': 'taap-heat-plan/1.0' } });
    res.status(r.status).setHeader('Content-Type', 'application/json');
    res.setHeader('Cache-Control', 'public, s-maxage=600');
    return res.send(await r.text());
  } catch (e) {
    return res.status(502).json({ error: true, reason: 'Open-Meteo unreachable' });
  }
};
